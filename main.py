import random
import string
import os
import socketio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI()
socket_app = socketio.ASGIApp(sio, app)

# เสิร์ฟไฟล์ static ตรงจาก Root Directory
app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/{filename:path}")
async def get_static_files(filename: str):
    if os.path.exists(filename):
        return FileResponse(filename)
    return FileResponse("index.html")

# --- โค้ดส่วนอื่นๆ คงไว้เหมือนเดิม ---
NOTES = ['C4', 'D4', 'E4', 'F4', 'G4', 'A4', 'B4']
SUITS = ['♥', '♠', '♣', '♦']

ACTION_CARDS = [
    {'type': 'bonus_100', 'title': '🎁 โบนัสคะแนน', 'desc': 'ได้รับคะแนนโบนัส +100 คะแนน!'},
    {'type': 'bonus_200', 'title': '💎 แจ็กพอต!', 'desc': 'ได้รับคะแนนโบนัสใหญ่ +200 คะแนน!'},
    {'type': 'steal_50', 'title': '🥷 ขโมยคะแนน', 'desc': 'ขโมยคะแนนจากผู้เล่นอีกฝ่าย 50 คะแนน!'},
    {'type': 'deduct_50', 'title': '💣 ระเบิดคะแนน', 'desc': 'สั่งลบคะแนนผู้เล่นอีกฝ่าย 50 คะแนน!'},
]

rooms = {}

def generate_room_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))

def create_new_board():
    cards_pool = NOTES * 3
    random.shuffle(cards_pool)
    return [
        {
            'id': idx,
            'note': note,
            'suit': random.choice(SUITS),
            'rank': random.randint(1, 10),
            'is_opened': False
        }
        for idx, note in enumerate(cards_pool)
    ]

@sio.event
async def create_room(sid, data):
    player_name = data.get('player_name') or 'ผู้เล่น 1'
    mode = data.get('mode', 'multi')
    
    room_id = generate_room_code() if mode == 'multi' else f"SINGLE_{random.randint(1000, 9999)}"
    await sio.enter_room(sid, room_id)
    
    rooms[room_id] = {
        'mode': mode,
        'players': [{'sid': sid, 'name': player_name, 'score': 0, 'role': 'p1'}],
        'cards': create_new_board(),
        'current_turn_sid': sid,
        'active_card': None
    }
    
    await sio.emit('room_created', {
        'room_id': room_id,
        'mode': mode,
        'role': 'p1',
        'players': rooms[room_id]['players'],
        'cards': rooms[room_id]['cards'],
        'current_turn_sid': sid
    }, room=sid)

@sio.event
async def join_room(sid, data):
    room_id = str(data.get('room_id', '')).upper().strip()
    player_name = data.get('player_name') or f"ผู้เล่น {len(rooms.get(room_id, {}).get('players', [])) + 1}"
    
    if room_id not in rooms:
        await sio.emit('error_msg', {'message': 'ไม่พบรหัสห้องนี้ กรุณาตรวจสอบอีกครั้ง'}, room=sid)
        return

    room = rooms[room_id]
    await sio.enter_room(sid, room_id)
    
    room['players'].append({
        'sid': sid,
        'name': player_name,
        'score': 0,
        'role': f"p{len(room['players']) + 1}"
    })
    
    await sio.emit('game_started', {
        'room_id': room_id,
        'mode': room['mode'],
        'players': room['players'],
        'cards': room['cards'],
        'current_turn_sid': room['current_turn_sid']
    }, room=room_id)

@sio.event
async def pick_card(sid, data):
    card_id = data.get('card_id')
    room_id = data.get('room_id')
    room = rooms.get(room_id)
    
    if not room:
        for r_id, r_data in rooms.items():
            if any(p['sid'] == sid for p in r_data['players']):
                room = r_data
                room_id = r_id
                break

    if not room:
        return

    if room['mode'] == 'multi' and room['current_turn_sid'] != sid:
        await sio.emit('error_msg', {'message': 'ยังไม่ถึงตาของคุณ!'}, room=sid)
        return

    card = next((c for c in room['cards'] if c['id'] == card_id), None)
    if card and not card['is_opened']:
        room['active_card'] = card
        await sio.emit('card_opened', {'card': card}, room=room_id)

@sio.event
async def submit_answer(sid, data):
    user_guess = data.get('guess')
    room_id = data.get('room_id')
    room = rooms.get(room_id)

    if not room:
        for r_id, r_data in rooms.items():
            if any(p['sid'] == sid for p in r_data['players']):
                room = r_data
                room_id = r_id
                break

    if not room or not room['active_card']:
        return

    active_card = room['active_card']
    correct_note = active_card['note']
    is_correct = (user_guess == correct_note)

    current_player = next((p for p in room['players'] if p['sid'] == sid), room['players'][0])
    
    reward_card = None
    if is_correct:
        reward_card = random.choice(ACTION_CARDS)
    else:
        current_player['score'] -= 20

    for c in room['cards']:
        if c['id'] == active_card['id']:
            c['is_opened'] = True

    removed_card_id = active_card['id']
    room['active_card'] = None

    if len(room['players']) > 1:
        curr_idx = next((i for i, p in enumerate(room['players']) if p['sid'] == sid), 0)
        next_idx = (curr_idx + 1) % len(room['players'])
        room['current_turn_sid'] = room['players'][next_idx]['sid']

    await sio.emit('answer_result', {
        'is_correct': is_correct,
        'correct_note': correct_note,
        'reward_card': reward_card,
        'players': room['players'],
        'removed_card_id': removed_card_id,
        'next_turn_sid': room['current_turn_sid']
    }, room=room_id)

    if not is_correct:
        opened_count = sum(1 for c in room['cards'] if c.get('is_opened', False))
        if opened_count >= len(room['cards']):
            await trigger_game_over(room_id)

@sio.event
async def select_reward(sid, data):
    room_id = data.get('room_id')
    reward_type = data.get('reward_type')
    room = rooms.get(room_id)
    
    if not room:
        return

    current_player = next((p for p in room['players'] if p['sid'] == sid), room['players'][0])
    other_players = [p for p in room['players'] if p['sid'] != sid]
    
    if reward_type == 'bonus_100':
        current_player['score'] += 100
    elif reward_type == 'bonus_200':
        current_player['score'] += 200
    elif reward_type == 'steal_50':
        current_player['score'] += 50
        if other_players:
            other_players[0]['score'] -= 50
    elif reward_type == 'deduct_50':
        if other_players:
            other_players[0]['score'] -= 50

    await sio.emit('update_scores', {
        'players': room['players'],
        'next_turn_sid': room['current_turn_sid']
    }, room=room_id)

    opened_count = sum(1 for c in room['cards'] if c.get('is_opened', False))
    if opened_count >= len(room['cards']):
        await trigger_game_over(room_id)

async def trigger_game_over(room_id):
    room = rooms.get(room_id)
    if not room:
        return

    players = room['players']
    winner_info = None

    if room['mode'] == 'single':
        winner_info = {'type': 'single', 'score': players[0]['score']}
    else:
        sorted_players = sorted(players, key=lambda p: p['score'], reverse=True)
        if len(sorted_players) > 1 and sorted_players[0]['score'] == sorted_players[1]['score']:
            winner_info = {'type': 'draw'}
        else:
            winner_info = {'type': 'win', 'winner_name': sorted_players[0]['name']}

    await sio.emit('game_over', {
        'winner_info': winner_info,
        'players': players
    }, room=room_id)

@sio.event
async def restart_game(sid, data):
    room_id = data.get('room_id')
    room = rooms.get(room_id)
    if not room:
        return

    room['cards'] = create_new_board()
    room['active_card'] = None
    for p in room['players']:
        p['score'] = 0

    await sio.emit('game_restarted', {
        'cards': room['cards'],
        'players': room['players'],
        'current_turn_sid': room['current_turn_sid']
    }, room=room_id)