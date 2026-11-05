import random
import string
import os
import socketio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# 1. Setup Socket.IO & FastAPI
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI()
socket_app = socketio.ASGIApp(sio, app)

# 2. Routing Static Files & UI Index
app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/")
async def read_index():
    return FileResponse("index.html")

# 3. Game Constants
NOTES = ['C4', 'D4', 'E4', 'F4', 'G4', 'A4', 'B4']
SUITS = ['♥', '♠', '♣', '♦']

ACTION_CARDS = [
    {'type': 'bonus_100', 'title': '🎁 โบนัสคะแนน', 'desc': 'ได้รับคะแนนโบนัส +100 คะแนน!'},
    {'type': 'bonus_200', 'title': '💎 แจ็กพอต!', 'desc': 'ได้รับคะแนนโบนัสใหญ่ +200 คะแนน!'},
    {'type': 'steal_50', 'title': '🏴‍☠️ ขโมยคะแนน', 'desc': 'ขโมยคะแนนจากผู้เล่นอื่น 50 คะแนน!'},
    {'type': 'deduct_50', 'title': '💣 ระเบิดคะแนน', 'desc': 'สั่งลบคะแนนผู้เล่นฝ่ายตรงข้าม 50 คะแนน!'},
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
async def connect(sid, environ):
    print(f"Client connected: {sid}")

@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")
    for room_id, room in list(rooms.items()):
        room['players'] = [p for p in room['players'] if p['sid'] != sid]
        if not room['players']:
            del rooms[room_id]
        else:
            await sio.emit('update_scores', {
                'players': room['players'],
                'next_turn_sid': room['current_turn_sid']
            }, room=room_id)

@sio.event
async def create_room(sid, data):
    player_name = data.get('player_name', 'Player')
    mode = data.get('mode', 'single')
    room_id = generate_room_code()

    rooms[room_id] = {
        'mode': mode,
        'players': [{'sid': sid, 'name': player_name, 'score': 0}],
        'cards': create_new_board(),
        'current_turn_sid': sid,
        'active_card': None
    }

    await sio.enter_room(sid, room_id)
    await sio.emit('room_created', {
        'room_id': room_id,
        'mode': mode,
        'players': rooms[room_id]['players'],
        'current_turn_sid': sid,
        'cards': rooms[room_id]['cards']
    }, room=sid)

@sio.event
async def join_room(sid, data):
    room_id = data.get('room_id', '').upper()
    player_name = data.get('player_name', 'Player')

    if room_id not in rooms:
        await sio.emit('error_msg', {'message': 'ไม่พบรหัสห้องนี้'}, room=sid)
        return

    room = rooms[room_id]
    if len(room['players']) >= 2:
        await sio.emit('error_msg', {'message': 'ห้องเต็มแล้ว'}, room=sid)
        return

    room['players'].append({'sid': sid, 'name': player_name, 'score': 0})
    await sio.enter_room(sid, room_id)

    await sio.emit('game_started', {
        'room_id': room_id,
        'players': room['players'],
        'current_turn_sid': room['current_turn_sid'],
        'cards': room['cards']
    }, room=room_id)

@sio.event
async def pick_card(sid, data):
    card_id = data.get('card_id')
    room_id = data.get('room_id')
    room = rooms.get(room_id)

    if not room:
        return

    if room['mode'] == 'multi' and room['current_turn_sid'] != sid:
        await sio.emit('error_msg', {'message': 'ยังไม่ถึงตาของคุณ!'}, room=sid)
        return

    if room.get('active_card') is not None:
        return

    card = next((c for c in room['cards'] if c['id'] == card_id), None)
    if card and not card['is_opened']:
        room['active_card'] = card
        await sio.emit('card_opened', {'card': card}, room=room_id)

@sio.event
async def submit_answer(sid, data):
    guess = data.get('guess')
    room_id = data.get('room_id')
    room = rooms.get(room_id)

    if not room or not room.get('active_card'):
        return

    card = room['active_card']
    is_correct = (guess == card['note'])
    current_player = next((p for p in room['players'] if p['sid'] == sid), None)

    reward_card = None
    if is_correct:
        card['is_opened'] = True
        if current_player:
            current_player['score'] += 100
        if random.random() < 0.3:
            reward_card = random.choice(ACTION_CARDS)
    else:
        if current_player:
            current_player['score'] = max(0, current_player['score'] - 20)

    room['active_card'] = None

    if room['mode'] == 'multi':
        players = room['players']
        idx = next((i for i, p in enumerate(players) if p['sid'] == sid), 0)
        next_sid = players[(idx + 1) % len(players)]['sid']
        room['current_turn_sid'] = next_sid
    else:
        next_sid = sid

    await sio.emit('answer_result', {
        'is_correct': is_correct,
        'correct_note': card['note'],
        'removed_card_id': card['id'],
        'players': room['players'],
        'next_turn_sid': next_sid,
        'reward_card': reward_card
    }, room=room_id)

    all_opened = all(c['is_opened'] for c in room['cards'])
    if all_opened:
        winner_info = get_winner_info(room)
        await sio.emit('game_over', {'winner_info': winner_info}, room=room_id)

@sio.event
async def select_reward(sid, data):
    reward_type = data.get('reward_type')
    room_id = data.get('room_id')
    room = rooms.get(room_id)

    if not room:
        return

    current_player = next((p for p in room['players'] if p['sid'] == sid), None)
    other_player = next((p for p in room['players'] if p['sid'] != sid), None)

    if current_player:
        if reward_type == 'bonus_100':
            current_player['score'] += 100
        elif reward_type == 'bonus_200':
            current_player['score'] += 200
        elif reward_type == 'steal_50' and other_player:
            stolen = min(50, other_player['score'])
            other_player['score'] -= stolen
            current_player['score'] += stolen
        elif reward_type == 'deduct_50' and other_player:
            other_player['score'] = max(0, other_player['score'] - 50)

    await sio.emit('update_scores', {
        'players': room['players'],
        'next_turn_sid': room['current_turn_sid']
    }, room=room_id)

@sio.event
async def restart_game(sid, data):
    room_id = data.get('room_id')
    room = rooms.get(room_id)

    if room:
        room['cards'] = create_new_board()
        room['active_card'] = None
        for p in room['players']:
            p['score'] = 0

        await sio.emit('game_restarted', {
            'cards': room['cards'],
            'players': room['players'],
            'current_turn_sid': room['current_turn_sid']
        }, room=room_id)

def get_winner_info(room):
    players = room['players']
    if room['mode'] == 'single':
        return {'type': 'single', 'score': players[0]['score']}
    
    if players[0]['score'] > players[1]['score']:
        return {'type': 'win', 'winner_name': players[0]['name']}
    elif players[1]['score'] > players[0]['score']:
        return {'type': 'win', 'winner_name': players[1]['name']}
    else:
        return {'type': 'draw'}