class_name MockPlayer

var bid: int = 0
var hands: int = 0
var legal_cards: Array = []
var cards: Array = []
var in_game_id: int = 0
var username: String = ""

var _is_me: bool = false


func _init(p_cards: Array, p_username: String, p_in_game_id: int):
	cards = p_cards
	username = p_username
	in_game_id = p_in_game_id


func is_me() -> bool:
	return _is_me
