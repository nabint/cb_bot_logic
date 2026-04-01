class_name MockGameHelper


enum Suit {
	DIAMONDS = 1,
	CLUBS,
	HEARTS,
	SPADES,
}

var _bid_player = null
var _max_card_played = null
var _next_player = null
var _current_suit = null
var _is_first_turn := false

# ---- Methods used by bot_logic ----
static func get_nth_next_array_item(arr, current_item, n):
	var current_item_index = arr.find(current_item)
	return arr[(current_item_index + n) % arr.size()]


func get_bid_player(p_dealer, p_players: Array, p_bid_turn: int):
	return get_nth_next_array_item(p_players, p_dealer, p_bid_turn + 1)


func get_right_side_player(p_player, p_players: Array):
	assert(p_players.size() == 4, "Needs exactly 4 players.")
	
	var player_index := p_players.find(p_player)
	# 0 -> 1
	# 1 -> 2
	# 2 -> 3
	# 3 -> 0
	var right_side_player_index: int = (player_index + 1) % 4
	return p_players[right_side_player_index]


static func get_player_by_in_game_id(p_players: Array, p_in_game_id: int):
	assert(p_in_game_id in [1, 2, 3, 4])
	var m_ret_player
	for player in p_players:
		if player.in_game_id == p_in_game_id:
			m_ret_player = player
			break
	
	return m_ret_player


static func get_winning_card(p_cards) -> String:
	var m_winning_card: String = p_cards[0]
	
	for m_card in p_cards:
		var m_win_rank: int = int(m_winning_card.substr(0, m_winning_card.length() - 1))
		var m_win_suit: int = get_suit_type(m_winning_card)
		
		var m_card_rank: int = int(m_card.substr(0, m_card.length() - 1))
		var m_card_suit: int = get_suit_type(m_card)
		
		if m_card_suit == m_win_suit and m_card_rank > m_win_rank:
			m_winning_card = m_card
		elif m_card_suit != m_win_suit and m_card_suit == Suit.SPADES:
			m_winning_card = m_card
	
	return m_winning_card


static func get_suit_type(p_card: String) -> int:
	var m_curr_card_suit: String = p_card[-1]
	
	match m_curr_card_suit:
		"S":
			return Suit.SPADES
		"H":
			return Suit.HEARTS
		"C":
			return Suit.CLUBS
		"D":
			return Suit.DIAMONDS
		_:
			return -1
