import json
import random
from datetime import datetime
from sqlalchemy.orm import Session
from ..models import ModelConfig, AIPlayer, Room, Game


def get_model_config(db: Session, config_id: int):
    return db.query(ModelConfig).filter(ModelConfig.id == config_id).first()


def create_model_config(db: Session, name: str, base_url: str, api_key: str):
    config = ModelConfig(name=name, base_url=base_url, api_key=api_key)
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def list_model_configs(db: Session):
    return db.query(ModelConfig).all()


def delete_model_config(db: Session, config_id: int):
    config = db.query(ModelConfig).filter(ModelConfig.id == config_id).first()
    if config:
        db.delete(config)
        db.commit()
    return config


def get_ai_player(db: Session, player_id: int):
    return db.query(AIPlayer).filter(AIPlayer.id == player_id).first()


def create_ai_player(db: Session, name: str, model_config_id: int, model_id: str, temperature: int = 70, reasoning_effort: str = ""):
    player = AIPlayer(name=name, model_config_id=model_config_id, model_id=model_id,
                      temperature=temperature, reasoning_effort=reasoning_effort)
    db.add(player)
    db.commit()
    db.refresh(player)
    return player


def list_ai_players(db: Session):
    return db.query(AIPlayer).all()


def delete_ai_player(db: Session, player_id: int):
    player = db.query(AIPlayer).filter(AIPlayer.id == player_id).first()
    if player:
        db.delete(player)
        db.commit()
    return player


def create_room(db: Session, mode: str, seats: list):
    room = Room(mode=mode, seats=json.dumps(seats))
    db.add(room)
    db.commit()
    db.refresh(room)
    return room


def get_room(db: Session, room_id: int):
    return db.query(Room).filter(Room.id == room_id).first()


def create_game(db: Session, room_id: int):
    game = Game(room_id=room_id)
    db.add(game)
    db.commit()
    db.refresh(game)
    return game


def get_game(db: Session, game_id: int):
    return db.query(Game).filter(Game.id == game_id).first()


def update_game_board(db: Session, game_id: int, board: list, turn: int, history: list, logs: list, winner: int = 0, status: str = "playing"):
    game = get_game(db, game_id)
    if game:
        game.board = json.dumps(board)
        game.turn = turn
        game.history = json.dumps(history)
        game.winner = winner
        game.logs = json.dumps(logs[-50:])
        game.status = status
        db.commit()
        db.refresh(game)
    return game
