class_name MockMatchInfoBot
extends Resource


var game_mode: int
var current_bid_turn: int
var current_game_round: int
var dealer


func _init(
	p_game_mode: int,
	p_current_bid_turn: int,
	p_current_game_round: int,
	p_dealer
) -> void:
	game_mode = p_game_mode
	current_bid_turn = p_current_bid_turn
	current_game_round = p_current_game_round
	dealer = p_dealer
