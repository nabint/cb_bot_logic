# -----------
# Bot logic based on probability.
# -----------
extends BotLogic


enum Strategy {
	PREPARE_FOR_CUT,
	COMPETE_SPADES,
	BRING_DOWN_ACE,
	BRING_DOWN_KING
}

enum Suit {
	DIAMONDS = 1,
	CLUBS,
	HEARTS,
	SPADES
}

enum GameMode {
	STANDARD = 1,
	QUICK,
	EIGHT_BID_CALL,
	EIGHT_BID_BREAK,
	TRAINING,
	TUTORIAL
}

const SPADE_PROBS: Dictionary = {
	14: 1.00,
	13: (0.832804 + 0.464285)/2.0,
	12: (0.654330 + 0.428571)/2.0,
	11: (0.485500 + 0.392857)/2.0,
	10: 0.281416,
	9: 0.225944,
	8: 0.232700,
	7: 0.177419,
	6: 0.126819,
	5: 0.091100,
	4: 0.070872,
	3: 0.050906,
	2: 0.030173,
}

const SUIT_MAP = {
	"D" : Suit.DIAMONDS,
	"C" : Suit.CLUBS,
	"H" : Suit.HEARTS,
	"S" : Suit.SPADES
}

var total_bid_score: float = 0.0
var total_power_score: float = 0.0
var total_cut_score: float = 0.0
var spades_score_info: Array = []
var confirm_score: float = 0.0 
var projected_score: float = 0.0

var _model: Dictionary = {}
var _game_helper: GameHelper
var _initial_cards_set: Array = []
var _unrevealed_cards: Dictionary = {}

var _diamonds_cards: Array = []
var _clubs_cards: Array = []
var _hearts_cards: Array = []
var _spades_cards: Array = []
#var _suit_cards:Array = [_diamonds_cards, _clubs_cards, _hearts_cards, _spades_cards] -samar
var _suit_cards: Dictionary = {
	Suit.DIAMONDS: _diamonds_cards,
	Suit.CLUBS: _clubs_cards,
	Suit.HEARTS: _hearts_cards,
	Suit.SPADES: _spades_cards
}

var _play_strategies: Array = []
var _game_starter
var _total_from_spades: float
var _no_of_cards_evaluated_for_bid: float = 0.0
var _no_of_cards_evaluated_from_spades: float = 0.0
var _no_of_cards_evaluated_from_cut: float = 0.0
var _no_of_cards_evaluated_from_power: float = 0.0
var _spare_spades: float = 0.0

var _cut_trick_played: Dictionary = {
	Suit.DIAMONDS: 0,
	Suit.CLUBS: 0,
	Suit.HEARTS: 0
}
var _suit_play_turns: Dictionary = {
	Suit.DIAMONDS: 0,
	Suit.CLUBS: 0,
	Suit.HEARTS: 0,
	Suit.SPADES: 0
}
var _match_players: Array
#var input_cards = [[12,6],[11,6,2],[8,7,6,3],[13,12,5,2]] #1 #re-evaluated 2
#var input_cards = [[11,3],[13,12,11,5],[13,8,6],[11,10,9,4]] #4  #re-evaluated 3
#var input_cards = [[9,8,7],[9,8,7],[12,10,6,5],[4,14,13]] #3 last bid turn total will be 13 
#var input_cards = [[10,4,3],[12,11,9],[9,6],[14,9,8,7,2]] #4
#var input_cards = [[14,12,6,5,4],[11,9,4],[11,4,3],[12,5]] #4


func _init(
	p_logic_holder: Player,
	p_game_helper: GameHelper,
	p_match_players: Array
).(p_logic_holder) -> void:
	_game_helper = p_game_helper
	_match_players = p_match_players


func initialize_with_json(p_json: Dictionary) -> void:
	_initial_cards_set = p_json["initial_cards_set"]
	
	var p_json_unrevealed_cards: Dictionary = p_json["unrevealed_cards"]
	var p_json_cut_trick_played: Dictionary = p_json["cut_trick_played"]
	var p_json_suit_play_turns: Dictionary = p_json["suit_play_turns"]
	
	for i in 4:
		var m_suit_str_unrevealed_cards: String = p_json_unrevealed_cards.keys()[i]
		_unrevealed_cards[int(m_suit_str_unrevealed_cards)] = p_json_unrevealed_cards[m_suit_str_unrevealed_cards]
		
		if i < 3:
			var m_suit_str_cut_trick_played: String = p_json_cut_trick_played.keys()[i]
			_cut_trick_played[int(m_suit_str_cut_trick_played)] = p_json_cut_trick_played[m_suit_str_cut_trick_played]
		
		var m_suit_str_suit_play_turns: String = p_json_suit_play_turns.keys()[i]
		_suit_play_turns[int(m_suit_str_suit_play_turns)] = p_json_suit_play_turns[m_suit_str_suit_play_turns]
	
	_diamonds_cards.clear()
	for m_card in p_json["diamonds_cards"]:
		_diamonds_cards.append(int(m_card))
	
	_clubs_cards.clear()
	for m_card in p_json["clubs_cards"]:
		_clubs_cards.append(int(m_card))
	
	_hearts_cards.clear()
	for m_card in p_json["hearts_cards"]:
		_hearts_cards.append(int(m_card))
	
	_spades_cards.clear()
	for m_card in p_json["spades_cards"]:
		_spades_cards.append(int(m_card))
	
	_play_strategies.clear()
	
	# refactor received strategies. -samar
	if !(p_json["play_strategies"].empty()):
		for i in p_json["play_strategies"]:
			assert(i.size() == 1)
			_play_strategies.append({
				int(i.keys()[0]): int(i.values()[0])
			})
	
#	var a = p_json["play_strategies"]
#	print(a)
#	print(_play_strategies)
	if (_play_strategies.size() != 0):
#		var s = _play_strategies[0].keys()[0]
#		print(typeof(s))
		assert(_play_strategies[0].keys().size() < 2)
		assert(typeof(_play_strategies[0].keys()[0]) == TYPE_INT)
#	_play_strategies.append_array(p_json["play_strategies"])
	
	var m_game_starter_id: int = p_json["game_starter_id"]
	if m_game_starter_id != -1:
		_game_starter = _game_helper.get_player_by_in_game_id(
			_match_players,
			m_game_starter_id
		)


func get_json() -> Dictionary:
	return {
		"initial_cards_set": _initial_cards_set,
		"unrevealed_cards": _unrevealed_cards,
		"diamonds_cards": _diamonds_cards,
		"clubs_cards": _clubs_cards,
		"hearts_cards": _hearts_cards,
		"spades_cards": _spades_cards,
		"play_strategies": _play_strategies,
		"game_starter_id": _game_starter.in_game_id if _game_starter else -1,
		"cut_trick_played": _cut_trick_played,
		"suit_play_turns": _suit_play_turns
	}


func _init_cards_evaluated() -> void:
	_no_of_cards_evaluated_for_bid = 0
	_no_of_cards_evaluated_from_spades = 0
	_no_of_cards_evaluated_from_cut = 0
	_no_of_cards_evaluated_from_power = 0


func _evaluate_spare_spades() -> void:
	_spare_spades = 1
	if _spades_cards.has(14):
		_spare_spades = 0.75
		if _spades_cards.has(13):
			_spare_spades = 0.5
			if _spades_cards.has(12):
				_spare_spades = 0.25
				if _spades_cards.has(11):
					_spare_spades = 0


func _evaluate_last_game_round(p_match_game_mode: int) -> int:
	if p_match_game_mode == GameMode.STANDARD:
		return 4
	else:
		return 2


func _remove_queen_probs(p_total_bid_score: float) -> float:
#	for suit in range(3): -samar
	for m_suit in Suit.values():
		if (m_suit == Suit.SPADES):
			continue
		if _suit_cards[m_suit].has(12):
			p_total_bid_score -= _prob_at_least(3, _suit_cards[m_suit].size())
	return p_total_bid_score


func _evaluate_total_bid_until_now() -> int:
	var m_total_bid_this_round = 0
	for player in _match_players:
		if player != _logic_holder:
			m_total_bid_this_round += player.bid
	return m_total_bid_this_round


# UNUSED METHOD: method not used for now might be useful later
#func _init_suit_cards() -> void:
#	for card in _logic_holder.cards:
#		var m_suit = SUIT_MAP[card[-1]]
#		var m_rank = int(card)
#		match m_suit:
#			Suit.SPADES:
#				_spades_cards.append(m_rank)
#			Suit.DIAMONDS:
#				_diamonds_cards.append(m_rank)
#			Suit.CLUBS:
#				_clubs_cards.append(m_rank)
#			Suit.HEARTS:
#				_hearts_cards.append(m_rank)


func compute_bid(
	cards: Array,
	p_match_info_obj: Match.MatchInfoBot,
	p_g_total_scores: Dictionary
) -> int:
	for m_card in cards:
		var m_suit = SUIT_MAP[m_card[-1]]
		var m_rank = int(m_card)
		match m_suit:
			Suit.SPADES:
				_spades_cards.append(m_rank)
			Suit.HEARTS:
				_hearts_cards.append(m_rank)
			Suit.CLUBS:
				_clubs_cards.append(m_rank)
			Suit.DIAMONDS:
				_diamonds_cards.append(m_rank)
	
	return select_bid_amount(
		p_match_info_obj,
		p_g_total_scores
	)


func clear_cards() -> void:
	_clear_bot_logic_data()


func select_bid_amount(
	p_match_info_object,
	p_g_total_scores: Dictionary
) -> int:
	total_bid_score = get_total_cards_win_probabilities()

	_total_from_spades = total_cut_score + projected_score
	
	#removing queen probs in high bid case
	if total_bid_score >= 5:
		total_bid_score = _remove_queen_probs(total_bid_score)
		
	var m_last_game_round: int = _evaluate_last_game_round(p_match_info_object.game_mode)
	
	var m_total_bid_this_round = 0
	if p_match_info_object.current_bid_turn == 3:
		m_total_bid_this_round = _evaluate_total_bid_until_now()
		total_bid_score = projected_score_context_adjustments(
			total_bid_score,
			m_total_bid_this_round
		)
	
#	if _spare_spades > 0.5 and confirm_score!= len(_spades_cards) and total_bid_score >= 6:
#		return int(max(1, _decide_final_bid(total_bid_score, m_last_game_round, m_total_bid_this_round, total_cut_score) - 1))

	return _decide_final_bid(
		total_bid_score,
		m_last_game_round,
		m_total_bid_this_round,
		total_cut_score,
		p_match_info_object.current_game_round,
		p_match_info_object.current_bid_turn,
		p_match_info_object.dealer,
		p_g_total_scores
	)


func get_total_cards_win_probabilities():
	total_bid_score = 0
	total_power_score = 0
	total_cut_score = 0
	
	_init_cards_evaluated()
	
	#test code
#	_diamonds_cards = input_cards[0]
#	_clubs_cards = input_cards[1]
#	_hearts_cards = input_cards[2]
#	_spades_cards = input_cards[3]

	_evaluate_spare_spades()
	
#	print("__________________________________")
#	print("Diamonds")

	var m_diamonds_power_score: float = _calculate_power_score(_diamonds_cards)
	total_power_score += m_diamonds_power_score
	var m_diamonds_cut_score: float = _calculate_cut_score(_diamonds_cards)
	total_cut_score += m_diamonds_cut_score
	
#	print("__________________________________")
#	print("Clubs")
	var m_clubs_power_score: float = _calculate_power_score(_clubs_cards)
	total_power_score += m_clubs_power_score
	var m_clubs_cut_score: float = _calculate_cut_score(_clubs_cards)
	total_cut_score += m_clubs_cut_score
	
#	print("__________________________________")
#	print("Hearts")
	var m_hearts_power_score: float = _calculate_power_score(_hearts_cards)
	total_power_score += m_hearts_power_score
	var m_hearts_cut_score: float = _calculate_cut_score(_hearts_cards)
	total_cut_score += m_hearts_cut_score
	
	total_cut_score = min(total_cut_score, len(_spades_cards) - _spare_spades)
	
#	var spades_score_info = _calculate_spades_score(_spades_cards, total_cut_score)
#	var confirm_score = spades_score_info[0]
#	var projected_score = spades_score_info[1]
	spades_score_info = _calculate_spades_score(_spades_cards, total_cut_score)
	confirm_score = spades_score_info[0]
	projected_score = spades_score_info[1]
	_total_from_spades = int(round(projected_score))
	
	#check if spades and cut evaluation exceeds no of spades cards
	if _no_of_cards_evaluated_from_cut + _no_of_cards_evaluated_from_spades > len(_spades_cards):
		if _no_of_cards_evaluated_from_spades == len(_spades_cards):
			_no_of_cards_evaluated_for_bid = len(_spades_cards) + _no_of_cards_evaluated_from_power
		else:
			_no_of_cards_evaluated_for_bid = ceil(len(_spades_cards) - _spare_spades + _no_of_cards_evaluated_from_power)
	else:
		_no_of_cards_evaluated_for_bid = _no_of_cards_evaluated_from_power + _no_of_cards_evaluated_from_cut + _no_of_cards_evaluated_from_spades
	
#	print("SORTED CARDS: ", _get_sorted_cards(_spades_cards, _diamonds_cards, _clubs_cards, _hearts_cards ))
	
#	print("_______BID SCORE FROM POWER IS_______ ",total_power_score )

	if total_cut_score > len(_spades_cards) - projected_score - _spare_spades:
		total_cut_score = len(_spades_cards) - projected_score - _spare_spades
		if total_cut_score < 0:
			total_cut_score = 0
	
	total_bid_score = total_power_score + total_cut_score + projected_score
	return total_bid_score


func projected_score_context_adjustments(
	p_my_total_bid_score: float,
	p_total_bid_this_round: int
) -> float:
	# var _initial_total = p_my_total_bid_score
	if p_my_total_bid_score >= 4:
		if round(p_total_bid_this_round + p_my_total_bid_score) == 12:
			p_my_total_bid_score -= 0.15
		if floor(p_total_bid_this_round + p_my_total_bid_score) == 10:
			p_my_total_bid_score += 0.15
			
	return p_my_total_bid_score 


func _decide_final_bid(
	p_total_bid_score: float,
	p_last_game_round: int,
	p_total_bid_this_round: int,
	p_total_cut_score: float,
	p_match_current_game_round: int,
	p_match_current_bid_turn: int,
	p_match_dealer: Player,
	p_g_total_scores: Dictionary
) -> int:
	var m_bid_final: int
	if _no_of_cards_evaluated_for_bid - p_total_bid_score <= 0.15:
#		print("Total bid amount is: ",_no_of_cards_evaluated_for_bid)
		m_bid_final = int(max(1, round(_no_of_cards_evaluated_for_bid)))	
		if p_match_current_game_round == p_last_game_round:
			m_bid_final = last_round_re_evaluate(
				m_bid_final,
				p_match_current_bid_turn,
				p_match_dealer,
				p_g_total_scores
			)
			return int(max(1, m_bid_final))

		if p_match_current_bid_turn == 3:	
			if (
				m_bid_final + p_total_bid_this_round <= 7 or
				m_bid_final + p_total_bid_this_round >= 13
			):
				m_bid_final = last_turn_re_evaluate(
					m_bid_final+ p_total_bid_this_round,
					p_total_bid_score,
					p_total_cut_score,
					m_bid_final
				)
				return int(max(1, m_bid_final))
		return int(max(1, m_bid_final))
		
	elif _no_of_cards_evaluated_for_bid - p_total_bid_score >= 0.6:
#		print("Total bid amount is: ",total_bid_score)
		m_bid_final = int(max(1, round(p_total_bid_score)))
		if p_match_current_game_round == p_last_game_round:
			m_bid_final = last_round_re_evaluate(
				m_bid_final,
				p_match_current_bid_turn,
				p_match_dealer,
				p_g_total_scores
			)
			return int(max(1, m_bid_final))

		if p_match_current_bid_turn == 3:
			if (
				m_bid_final + p_total_bid_this_round <= 7 or
				m_bid_final+ p_total_bid_this_round >= 13
			): 
				m_bid_final = last_turn_re_evaluate(
					m_bid_final + p_total_bid_this_round,
					p_total_bid_score,
					p_total_cut_score,
					m_bid_final
				)
				return int(max(1, m_bid_final))
		return int(max(1, m_bid_final))
	
	else:
#		print("Total bid amount is: ", total_bid_score)
		m_bid_final = int(max(1, floor(p_total_bid_score)))
		if p_match_current_game_round == p_last_game_round:
			m_bid_final = last_round_re_evaluate(
				m_bid_final,
				p_match_current_bid_turn,
				p_match_dealer,
				p_g_total_scores
			)
			return int(max(1, m_bid_final))
		
		if p_match_current_bid_turn == 3:
			if (
				m_bid_final + p_total_bid_this_round <= 7 or
				m_bid_final + p_total_bid_this_round >= 13
			): 
				m_bid_final = last_turn_re_evaluate(
					m_bid_final+ p_total_bid_this_round,
					p_total_bid_score,
					p_total_cut_score,
					m_bid_final
				)
				return int(max(1, m_bid_final))
		return int(max(1, m_bid_final))


func last_round_re_evaluate(
	p_my_bid: int,
	p_match_current_bid_turn: int,
	p_match_dealer: Player,
	p_g_total_scores: Dictionary
) -> int:
	# var _initial_total = p_my_bid
	var m_opponents: Array = []
	var m_my_projected_score = (
		p_g_total_scores[_logic_holder.in_game_id]
		+ p_my_bid
	)
	var m_opponents_projected_score:Array = []
	for i in range(p_match_current_bid_turn):
		m_opponents.append(
			_game_helper.get_bid_player(p_match_dealer, _match_players, i)
		)
		m_opponents_projected_score.append(
			p_g_total_scores[m_opponents[i].in_game_id] + m_opponents[i].bid
		)
	
	if p_match_current_bid_turn == 1:	
		if (
			m_opponents_projected_score[0] > m_my_projected_score and
			m_opponents_projected_score[0] - m_my_projected_score < 1
		):
			p_my_bid += 1
		
	if p_match_current_bid_turn == 2:
		if (
			(
				m_opponents_projected_score[0] > m_my_projected_score and
				m_opponents_projected_score[0] - m_my_projected_score < 1
			) or
			(
				m_opponents_projected_score[1] > m_my_projected_score and
				m_opponents_projected_score[1] - m_my_projected_score < 1
			)
		):
			p_my_bid += 1
		
		elif (
			m_my_projected_score > m_opponents_projected_score[0] + 2 and
			m_my_projected_score > m_opponents_projected_score[1] + 2
		):
			var last_opponent = _game_helper.get_bid_player(
				p_match_dealer,
				_match_players,
				3
			)
			if (
				p_g_total_scores[last_opponent.in_game_id] + 4
				<= m_my_projected_score
			):
				p_my_bid -= 1
		
	if p_match_current_bid_turn == 3:
		if (
			(
				m_opponents_projected_score[0] > m_my_projected_score and
				m_opponents_projected_score[0] - m_my_projected_score < 1
			) or
			(
				m_opponents_projected_score[1] > m_my_projected_score and
				m_opponents_projected_score[1] - m_my_projected_score < 1
			) or
			(
				m_opponents_projected_score[2] > m_my_projected_score and
				m_opponents_projected_score[2] - m_my_projected_score < 1
			)
		):
			p_my_bid += 1
		elif (
			m_my_projected_score > m_opponents_projected_score[0] + 2 and
			m_my_projected_score > m_opponents_projected_score[1] + 2 and
			m_my_projected_score > m_opponents_projected_score[2] + 2
		):
			p_my_bid -= 1
	
	m_my_projected_score = p_g_total_scores[_logic_holder.in_game_id] + p_my_bid
	if m_my_projected_score < 0 and 0 - m_my_projected_score < 3:
		p_my_bid += (0 - m_my_projected_score)
	
	return int(round(p_my_bid))


func last_turn_re_evaluate(
	p_projected_total: int,
	p_my_total: float,
	p_total_cut_score: float,
	_p_bid_final: int
) -> int:
	# var _initial_total = p_bid_final
#	var suit_cards = [_diamonds_cards, _clubs_cards, _hearts_cards, _spades_cards] -samar
	var m_suit_cards: Dictionary = {
		Suit.DIAMONDS: _diamonds_cards,
		Suit.CLUBS: _clubs_cards,
		Suit.HEARTS: _hearts_cards,
		Suit.SPADES: _spades_cards
	}
	if p_projected_total <= 7:
#		for suit in range(3): -samar
		for suit in Suit.values():
			if (suit == Suit.SPADES):
				continue
			if m_suit_cards[suit].has(13):
				if m_suit_cards[suit].has(12) or m_suit_cards[suit].has(14):
#					if m_suit_cards.size() >= 2:
						p_my_total += (
							1 - _prob_at_least(2, m_suit_cards[suit].size())
						)
				elif m_suit_cards[suit].size() >= 2:
					p_my_total += (
						0.862628 - _prob_at_least(2, m_suit_cards[suit].size())
					)
			if m_suit_cards[suit].has(12):
				if m_suit_cards[suit].size() in [3, 4]:
					p_my_total += (
						0.27687 - _prob_at_least(3, m_suit_cards[suit].size())
					)
			if m_suit_cards[suit].size() <= 2:
				if (
					m_suit_cards[Suit.SPADES].size()
					- (_total_from_spades + p_total_cut_score) > 1
				):
					p_my_total += (1 - 0.650359)
	
	if p_projected_total >=13:
#		for suit in range(3): -samar
		for suit in Suit.values():
			if (suit == Suit.SPADES):
				continue
			if m_suit_cards[suit].has(14):
				p_my_total -= (1 - _prob_at_least(1, m_suit_cards[suit].size()))
			if m_suit_cards[suit].has(13):
				if m_suit_cards[suit].size() == 2:
					p_my_total -= (
						0.4 * _prob_at_least(2, m_suit_cards[suit].size())
					)
				if m_suit_cards[suit].size() == 3:
					p_my_total -= (
						0.6 * _prob_at_least(2, m_suit_cards[suit].size())
					)
				if m_suit_cards[suit].size() == 4:
					p_my_total -= (
						0.8 * _prob_at_least(2, m_suit_cards[suit].size())
					)
				if m_suit_cards[suit].size() > 4:
					p_my_total -= _prob_at_least(2, m_suit_cards[suit].size())
			if m_suit_cards[suit].has(12):
				p_my_total -= _prob_at_least(3, m_suit_cards[suit].size())
	
	return int(round(p_my_total))


func select_throw_card(
	p_match_current_throw_turn: int,
	p_match_dealer: Player,
	p_match_played_cards: Array
) -> String:
	var m_selected_card: String
#	var m_game_starter: Player
	if _logic_holder.legal_cards.size() == 1:
		m_selected_card = _logic_holder.legal_cards[0]
	
	else:
		var m_non_spades_legal_cards: Array = []
		for m_each in _logic_holder.legal_cards:
			if SUIT_MAP[m_each[-1]] != Suit.SPADES:
				m_non_spades_legal_cards.append(m_each)
		
		if p_match_current_throw_turn == 0:
	#		print(_logic_holder.username, " PLAY STRATEGIES ", _play_strategies)
			m_selected_card = _get_card_from_first_throw_turn_logic(
				m_non_spades_legal_cards,
				p_match_dealer
			)
		
		elif p_match_current_throw_turn == 1:
			m_selected_card = _get_card_from_second_throw_turn_logic(
				p_match_played_cards
			)
		
		elif p_match_current_throw_turn == 2:
			m_selected_card = _get_card_from_third_throw_turn_logic(
				p_match_played_cards
			)
		
		elif p_match_current_throw_turn == 3:
			m_selected_card = _get_card_from_last_throw_turn_logic(
				p_match_played_cards
			)
		
		elif m_non_spades_legal_cards.size() != 0:
			m_selected_card = _get_non_spades_random_card(
				m_non_spades_legal_cards,
				p_match_dealer
			)
		else:
			m_selected_card = _get_random_card(_logic_holder.legal_cards)
	
	assert(_logic_holder.legal_cards.has(m_selected_card))
#	print(_logic_holder.type)
#	print(_suit_cards)
#	print(_play_strategies)
#	print()
	# Breakpoint here for testing -samar
	return m_selected_card


func _get_non_spades_random_card(
	p_non_spades_legal_cards: Array,
	p_match_dealer: Player
) -> String:
#	print_debug("-> Getting non spades random card.")
	var m_card: String
#	for suit in range(3): -samar
	for suit in Suit.values():
		if (suit == Suit.SPADES):
			continue
		if (
			_suit_cards[suit].has(_get_senior_card(suit)) and
			_cut_trick_played[suit] == 0
		):
			if _suit_play_turns[suit] < 2:
				m_card = str(_get_senior_card(suit)) + _get_suit_letter(suit)
				return m_card
			if (
				_suit_play_turns[suit] == 2 and
				_logic_holder == _game_helper.get_bid_player(
					p_match_dealer,
					_match_players,
					3
				)
			):
				var m_total_bid := 0
				for players in _match_players:
					m_total_bid += players.bid
				if m_total_bid < 8:
					m_card = (
						str(_get_senior_card(suit)) + _get_suit_letter(suit)
					)
					return m_card
	
	return _get_random_card(p_non_spades_legal_cards)


# UNUSED METHOD: method not used for now might be useful later
#func _get_total_bid() -> void:
#	# warning-ignore: UNUSED_VARIABLE
#	var m_total_bid: int = 0
#
#	for player in _match_players:
#		m_total_bid += player.bid


func _get_card_from_first_throw_turn_logic(
	p_non_spades_legal_cards: Array,
	p_match_dealer: Player
) -> String:
#	print_debug("-> Getting card from first throw turn logic.")
	var m_card: String
#	print("HANDS: ",_logic_holder.hands)
#	print("BID: ",_logic_holder.bid)
	
	#no spades remain with opponents and you're the only one having spades
	if (
		(
			_unrevealed_cards[Suit.SPADES].size()
			== _suit_cards[Suit.SPADES].size()
		) and 
		_suit_cards[Suit.SPADES].size() > 0
	):
		if (
			_logic_holder.bid - _logic_holder.hands
			<= _suit_cards[Suit.SPADES].size()
		):
			pass
		else:
			m_card = (
				str(_max_arr(_suit_cards[Suit.SPADES]))
				+ _get_suit_letter(Suit.SPADES)
			)
			return m_card
	
	#only one spades remain with opponents and you're the one having senior spades
	if (
		(
			_unrevealed_cards[Suit.SPADES].size()
			- _suit_cards[Suit.SPADES].size() == 1
		) and
		_suit_cards[Suit.SPADES].size() > 0 and
		_suit_cards[Suit.SPADES].has(_get_senior_card(Suit.SPADES))
	):
		m_card = (
			str(_max_arr(_suit_cards[Suit.SPADES]))
			+ _get_suit_letter(Suit.SPADES)
		)
		return m_card
	
#	for suit in range(3): -samar
	for suit in Suit.values():
		if (suit == Suit.SPADES):
			continue
		#no spades remain with opponents and you're the one with senior m_card of a non spades suit
		if (
			_suit_cards[suit].size() > 0 and
			_suit_cards[suit].has(_get_senior_card(suit)) and
			(
				_unrevealed_cards[Suit.SPADES].size() 
				== _suit_cards[Suit.SPADES].size()
			)
		):
			m_card = str(_max_arr(_suit_cards[suit])) + _get_suit_letter(suit)
			return m_card
		
		#creating spades fight between players anticipating a win from cut trick
		if (
			_suit_cards[suit].size() > 0 and
			_suit_play_turns[suit] > 2 and
			!_suit_cards[suit].has(_get_senior_card(suit)) and
			(
				_unrevealed_cards[Suit.SPADES].size()
				- _suit_cards[Suit.SPADES].size()
			) >= 2
		):
			m_card = str(_min_arr(_suit_cards[suit])) + _get_suit_letter(suit)
			return m_card
	
	if _logic_holder.hands >= _logic_holder.bid:
		if _suit_cards[Suit.SPADES].size() > 0:
#			print("____HANDS WON UNTIL NOW____", _logic_holder.hands)
#			print("____BID THIS GAME____", _logic_holder.bid)
			m_card = (
				str(_min_arr(_suit_cards[Suit.SPADES]))
				+ _get_suit_letter(Suit.SPADES)
			)
			return m_card
		
		#creating spades fight between players anticipating a win from cut trick
#		for suit in range(3): -samar
		for suit in Suit.values():
			if (suit == Suit.SPADES):
				continue
			if (
				_cut_trick_played[suit] == 1 and
				_suit_cards[suit].size() > 0 and
				(
					_unrevealed_cards[Suit.SPADES].size()
					- _suit_cards[Suit.SPADES].size() >= 2
				)
			):
				m_card = (
					str(_min_arr(_suit_cards[suit])) + _get_suit_letter(suit)
				)
				return m_card
	
	if (
		self._spades_cards.size() > 1 and
		self._spades_cards.has(_get_senior_card(Suit.SPADES)) and
		self._spades_cards.has(_unrevealed_cards[Suit.SPADES][1])
	):
		m_card = str(_compete_spades()) + _get_suit_letter(Suit.SPADES)
		var m_index = 0
		for i in _play_strategies:
			var strategy = i.keys()[0]
			var suit = i.values()[0]
			if (strategy == Strategy.COMPETE_SPADES and suit == Suit.SPADES):
				_play_strategies.remove(m_index)
				break
			m_index += 1
##		if _play_strategies.has({"compete_spades": CONFIG.Suit.SPADES}): -samar strat
#		if _play_strategies.has({Strategy.COMPETE_SPADES: CONFIG.Suit.SPADES}):
##			_play_strategies.erase({"compete_spades": CONFIG.Suit.SPADES}) -samar strat
#			_play_strategies.erase({Strategy.COMPETE_SPADES: CONFIG.Suit.SPADES})
		return m_card
	
	if _play_strategies.size() != 0:
		var m_rand_num = randi() % _play_strategies.size()
		var m_play_strategy = _play_strategies[m_rand_num]
		var __: bool
		for key in m_play_strategy.keys():
#			print(typeof(key))
#			print()
			if _suit_cards[m_play_strategy[key]].size() != 0:
				if _suit_cards[m_play_strategy[key]].size() == 1:
#					if key in ["prepare_for_cut","compete_spades"]: -samar strat
					if (
						key in
						[Strategy.PREPARE_FOR_CUT, Strategy.COMPETE_SPADES]
					):
						m_card = _get_play_strategy_card(m_play_strategy)
					else:
						if (
							_suit_cards[m_play_strategy[key]].has(
								_get_senior_card(m_play_strategy[key])
							)
						):
							m_card = _get_play_strategy_card(m_play_strategy)	
						elif (
							_suit_cards[m_play_strategy[key]].has(
								_get_senior_card(m_play_strategy[key]) - 1
							)
						):
							if p_non_spades_legal_cards.size() != 0:
								m_card = _get_non_spades_random_card(
									p_non_spades_legal_cards,
									p_match_dealer
								)
						else:
							m_card = _get_play_strategy_card(m_play_strategy)
				else:
					m_card = _get_play_strategy_card(m_play_strategy)
			
			else:
#				_play_strategies.erase(m_play_strategy)
				if p_non_spades_legal_cards.size() != 0:
					m_card = _get_non_spades_random_card(
						p_non_spades_legal_cards,
						p_match_dealer
					)
					return m_card
				m_card = str(_compete_spades()) + _get_suit_letter(Suit.SPADES)
				
				# dictionary compare bug fix -samar
				var m_index = 0
				for i in _play_strategies:
					var m_strategy = i.keys()[0]
					var m_suit = i.values()[0]
					if (
						m_strategy == Strategy.COMPETE_SPADES and
						m_suit == Suit.SPADES
					):
						_play_strategies.remove(m_index)
						break
					m_index += 1
				
#				if _play_strategies.has({"compete_spades": CONFIG.Suit.SPADES}): -samar strat
#				if _play_strategies.has({Strategy.COMPETE_SPADES: CONFIG.Suit.SPADES}):
##					_play_strategies.erase({"compete_spades": CONFIG.Suit.SPADES}) -samar strat
#					_play_strategies.erase({Strategy.COMPETE_SPADES: CONFIG.Suit.SPADES})
#				print(_play_strategies)
				return m_card
#				return _get_random_card()
			_play_strategies.remove(m_rand_num)
			return m_card
	
	else:
		#either dont throw cards if you have senior m_card from a suit
		#or use the senior m_card to secure point based on no of turns of this suit has happened
#		for i in range(3): -samar
		for suit in Suit.values():
			if (suit == Suit.SPADES):
				continue
			if (
				_suit_cards[suit].has(_get_senior_card(suit)) and
				_suit_play_turns[suit] == 0 and
				_unrevealed_cards[suit].size() < 13
			):
				m_card = str(_get_senior_card(suit)) + _get_suit_letter(suit)
				return m_card
	
	if p_non_spades_legal_cards.size() != 0:
		m_card = _get_non_spades_random_card(
			p_non_spades_legal_cards,
			p_match_dealer
		)
		return m_card
	m_card = str(_compete_spades()) + _get_suit_letter(Suit.SPADES)
	# dictionary compare bug fix -samar
	var m_index = 0
	for i in _play_strategies:
		var m_strategy = i.keys()[0]
		var m_suit = i.values()[0]
		if (m_strategy == Strategy.COMPETE_SPADES and m_suit == Suit.SPADES):
			_play_strategies.remove(m_index)
			break
		m_index += 1
#	if _play_strategies.has({"compete_spades": CONFIG.Suit.SPADES}): -samar strat
#	if _play_strategies.has({Strategy.COMPETE_SPADES: CONFIG.Suit.SPADES}):
##		_play_strategies.erase({"compete_spades": CONFIG.Suit.SPADES}) -samar strat
#		_play_strategies.erase({Strategy.COMPETE_SPADES: CONFIG.Suit.SPADES})
#	print(_play_strategies)
	return m_card
#	return _get_random_card()


func _get_card_from_second_throw_turn_logic(
	p_match_played_cards: Array
) -> String:
#	print_debug("-> Getting card from second throw turn logic.")
	var m_card: String
	var m_first_throw_card_suit = SUIT_MAP[p_match_played_cards[0][-1]]
	
	if m_first_throw_card_suit != Suit.SPADES:
		if _suit_cards[m_first_throw_card_suit].size() != 0:
			m_card = _play_safe_card(
				m_first_throw_card_suit,
				p_match_played_cards
			)
			return m_card
		
		else:
			if _suit_cards[Suit.SPADES].size() != 0:
#				m_card = str(_get_smallest_from_legal_cards()) + _get_suit_letter(CONFIG.Suit.SPADES) ###Testinf confuser m_card
				m_card = get_confuser_card(
					int(_get_smallest_from_legal_cards(p_match_played_cards)),
					Suit.SPADES
				)
				return m_card
			else:
				m_card = (
					str(_on_cut_chance_but_have_no_spades(
						m_first_throw_card_suit
					))
				)
				return m_card
		
	else: 
		if _suit_cards[Suit.SPADES].size() != 0:
			m_card = _play_spades_safely(p_match_played_cards)
			return m_card
		else: 
			m_card = (
				str(_on_cut_chance_but_have_no_spades(m_first_throw_card_suit))
			)
			return m_card


func _get_card_from_third_throw_turn_logic(
	p_match_played_cards: Array
) -> String:
#	print_debug("-> Getting card from third throw turn logic.")
	
	var m_first_throw_card_suit = SUIT_MAP[p_match_played_cards[0][-1]]
	var m_cards_on_floor = p_match_played_cards.duplicate(true)
	
	if m_first_throw_card_suit != Suit.SPADES:
		if _suit_cards[m_first_throw_card_suit].size() != 0:
			if SUIT_MAP[p_match_played_cards[1][-1]] != Suit.SPADES:
				return _play_safe_card(
					m_first_throw_card_suit,
					p_match_played_cards
				)
			
			else:
#				return _get_smallest_from_legal_cards() + _get_suit_letter(m_first_throw_card_suit) ###Testing confuser card
				return get_confuser_card(
					int(_get_smallest_from_legal_cards(p_match_played_cards)),
					m_first_throw_card_suit
				)
		
		else:
			if _suit_cards[Suit.SPADES].size() != 0:
				#you have spades but your spades are not enough to win 
				m_cards_on_floor.append(
					str(_max_arr(_suit_cards[Suit.SPADES])) + "S"
				)
				if (
					m_cards_on_floor[2]
					== _game_helper.get_winning_card(m_cards_on_floor)
				):
#					return _get_smallest_from_legal_cards() + _get_suit_letter(CONFIG.Suit.SPADES)###Testing confuser card
					return get_confuser_card(
						int(_get_smallest_from_legal_cards(p_match_played_cards)),
						Suit.SPADES
					)
				
				else:
					if _is_all_spades_in_legal_cards():
#						return _get_smallest_from_legal_cards() + _get_suit_letter(CONFIG.Suit.SPADES)###Testing confuser card
						return get_confuser_card(
							int(_get_smallest_from_legal_cards(p_match_played_cards)),
							Suit.SPADES
						)
					else:
						return str(
							_on_cut_chance_but_have_no_spades(
								m_first_throw_card_suit
							)
						)
				
			else:
				return str(
					_on_cut_chance_but_have_no_spades(m_first_throw_card_suit)
				)
	else:
		if _suit_cards[Suit.SPADES].size() != 0:
			return _play_spades_safely(p_match_played_cards)
		
		else:
			return str(
				_on_cut_chance_but_have_no_spades(m_first_throw_card_suit)
			)


func _get_card_from_last_throw_turn_logic(
	p_match_played_cards: Array
) -> String:
#	print_debug("-> Getting card from last throw turn logic.")
	var m_card: String
	var m_first_throw_card_suit = SUIT_MAP[p_match_played_cards[0][-1]]
	
	if _suit_cards[m_first_throw_card_suit].size() == 0:
		if _suit_cards[Suit.SPADES].size() == 0:
			#if you have cut chance but spades is also finished which m_card will you throw from ramaining suits
			#throw smallest from remaining cards with you
			m_card = str(
				_on_cut_chance_but_have_no_spades(m_first_throw_card_suit)
			)
		elif _is_all_spades_in_legal_cards():
#			m_card = _get_smallest_from_legal_cards() + _get_suit_letter(CONFIG.Suit.SPADES)##Testing for confuser m_card
			m_card = get_confuser_card(
				int(_get_smallest_from_legal_cards(p_match_played_cards)),
				Suit.SPADES
			)
		else:
			#means you dont have first throw m_card suit and your spades are smaller than previously thrown spades in this play turn
			#so you can either throw small spades or smallest m_card of any other suit
			#this condition may not arise, consult RULE for this case
#			m_card = _logic_holder.legal_cards[randi() % _logic_holder.legal_cards.size()]	
			m_card = str(
				_on_cut_chance_but_have_no_spades(m_first_throw_card_suit)
			)
	else:
#		m_card = _get_smallest_from_legal_cards() + _get_suit_letter(m_first_throw_card_suit)##Testing for confuser m_card
		m_card = get_confuser_card(
			int(_get_smallest_from_legal_cards(p_match_played_cards)),
			m_first_throw_card_suit
		)
		print(_unrevealed_cards)
#		if _suit_cards[m_first_throw_card_suit].has(int(_get_smallest_from_legal_cards()) + 1):
#			m_card = str(int(_get_smallest_from_legal_cards()) + 1) + _get_suit_letter(first_throw_card_suit)
	return m_card


func get_confuser_card(smallest_rank, suit):
	var m_confuser_card = smallest_rank
	
	for m_rank in range(smallest_rank + 1,_suit_cards[suit][0] + 1):
		if m_rank in _unrevealed_cards[suit]:
			if m_rank in _suit_cards[suit]:
				m_confuser_card = m_rank
			else:
				return str(m_confuser_card) +_get_suit_letter(suit)
	return str(m_confuser_card) + _get_suit_letter(suit)


func _on_cut_chance_but_have_no_spades(_p_first_throw_card_suit: int) -> String:
	# -samar the parameter is never used
	# -samar changed suit_sizes type to Dictionary
	var m_suit_sizes: Dictionary = {
		Suit.DIAMONDS: 0,
		Suit.CLUBS: 0,
		Suit.HEARTS: 0,
		Suit.SPADES: 0
	}
	var m_max_suit_size: int
	var m_max_size_suits_ids: Array = []
#	for i in range(3): -samar
	for suit in Suit.values():
		if (suit == Suit.SPADES):
			continue
		m_suit_sizes[suit] = _suit_cards[suit].size()
	
	m_max_suit_size = _max_arr(m_suit_sizes.values())
#	for i in range(3): -samar
	for suit in Suit.values():
		if (suit == Suit.SPADES):
			continue
		if m_suit_sizes[suit] == m_max_suit_size:
			m_max_size_suits_ids.append(suit)
	
	if m_max_size_suits_ids.size() == 1:
		return (
			str(_min_arr(_suit_cards[m_max_size_suits_ids[0]]))
			+ _get_suit_letter(m_max_size_suits_ids[0])
		)
	
	if m_max_size_suits_ids.size() == 2:
		var rank = min(
			_min_arr(_suit_cards[m_max_size_suits_ids[0]]),
			_min_arr(_suit_cards[m_max_size_suits_ids[1]])
		)
		if rank == _min_arr(_suit_cards[m_max_size_suits_ids[0]]):
			return str(rank) + _get_suit_letter(m_max_size_suits_ids[0])
		else:
			return str(rank) + _get_suit_letter(m_max_size_suits_ids[1])
	
	if m_max_size_suits_ids.size() == 3:
		var rank = _min_arr(
			[_min_arr(_suit_cards[m_max_size_suits_ids[0]]),
			_min_arr(_suit_cards[m_max_size_suits_ids[1]]),
			_min_arr(_suit_cards[m_max_size_suits_ids[2]])]
		)
		if rank == _min_arr(_suit_cards[m_max_size_suits_ids[0]]):
			return str(rank) + _get_suit_letter(m_max_size_suits_ids[0])
		elif rank == _min_arr(_suit_cards[m_max_size_suits_ids[1]]):
			return str(rank) + _get_suit_letter(m_max_size_suits_ids[1])
		else:
			return str(rank) + _get_suit_letter(m_max_size_suits_ids[2])
	
	return ""


func _play_spades_safely(p_match_played_cards: Array) -> String:
	var m_card: String = ""
	var m_legal_card_ranks:Array = []
	var m_cards_on_floor = p_match_played_cards.duplicate(true)
	for each in _logic_holder.legal_cards:
		m_legal_card_ranks.append(int(each))
		
	if m_legal_card_ranks.has(_get_senior_card(Suit.SPADES)):
		m_card = (
			str(_get_senior_card(Suit.SPADES)) + _get_suit_letter(Suit.SPADES)
		)
		m_cards_on_floor.append(m_card)
		if m_card == _game_helper.get_winning_card(m_cards_on_floor):
			return m_card
		else:
#			m_card = _get_smallest_from_legal_cards() + _get_suit_letter(CONFIG.Suit.SPADES)####Confuser m_card test
			m_card = get_confuser_card(
				int(_get_smallest_from_legal_cards(p_match_played_cards)),
				Suit.SPADES
			)
			return m_card
			
	else:
		for i in range(
			_get_largest_of_mycards_from_this_suit(Suit.SPADES) -1,
			1,
			-1
		):
			if m_legal_card_ranks.has(i) and m_legal_card_ranks.has(i+1):
				continue
			if m_legal_card_ranks.has(i) and !m_legal_card_ranks.has(i+1):
				m_card = str(i) + _get_suit_letter(Suit.SPADES)
				break	
			m_card = (
				str(_get_2nd_largest(m_legal_card_ranks))
				+ _get_suit_letter(Suit.SPADES)
			)
		
		if m_card == "":
			m_card = (
				str(_get_2nd_largest(m_legal_card_ranks))
				+ _get_suit_letter(Suit.SPADES)
			)
			
		m_cards_on_floor.append(m_card)
		if m_card == _game_helper.get_winning_card(m_cards_on_floor):
			return m_card
		else:
#			m_card = _get_smallest_from_legal_cards() + _get_suit_letter(CONFIG.Suit.SPADES)####Confuser m_card test
			m_card = get_confuser_card(
				int(_get_smallest_from_legal_cards(p_match_played_cards)),
				Suit.SPADES
			)
			return m_card


func _play_safe_card(p_suit_id: int, p_match_played_cards: Array) -> String:
	var m_card: String
	var m_cards_on_floor = p_match_played_cards.duplicate(true)
	var m_legal_card_ranks:Array = []
	for each in _logic_holder.legal_cards:
		m_legal_card_ranks.append(int(each))
	if _suit_play_turns[p_suit_id] == 1:
		if _is_suit_has_ace(m_legal_card_ranks):
			if (
				_is_suit_has_queen(m_legal_card_ranks) and
				_suit_cards[p_suit_id].size() < 5
			):
				m_card = "12" + _get_suit_letter(p_suit_id)
				return m_card
			else:
				m_card = "14" + _get_suit_letter(p_suit_id)
				return m_card
		else:
			if (
				_is_suit_has_king(_suit_cards[p_suit_id]) or
				_is_suit_has_queen(_suit_cards[p_suit_id])
			):
				if !p_match_played_cards.has("14" + _get_suit_letter(p_suit_id)):
					m_card = (
						str(_get_2nd_largest(m_legal_card_ranks))
						+ _get_suit_letter(p_suit_id)
					)
					return m_card
	
	if (
		_suit_play_turns[p_suit_id] >=2 and
		(
			_get_largest_of_mycards_from_this_suit(p_suit_id) 
			== _get_senior_card(p_suit_id)
		) and
		_cut_trick_played[p_suit_id] == 0
	):
		m_card = str(_get_senior_card(p_suit_id)) + _get_suit_letter(p_suit_id)
		m_cards_on_floor.append(m_card)
		if m_card == _game_helper.get_winning_card(m_cards_on_floor):
			return m_card
	
#	m_card = _get_smallest_from_legal_cards() + _get_suit_letter(p_suit_id) ### Testing confuser m_card logic
	m_card = get_confuser_card(
		int(_get_smallest_from_legal_cards(p_match_played_cards)),
		p_suit_id
	)
	return m_card


func _on_deal_completed() -> void:
	_initial_cards_set = _logic_holder.cards.duplicate(true)
#	_setup_model()
	_init_unrevealed_cards()
	_separate_cards_according_to_suits()


func _separate_cards_according_to_suits() -> void:
#	print("cards separated according to suits...")
	for m_card in _logic_holder.cards:
		var m_suit = SUIT_MAP[m_card[-1]]
		var m_rank = int(m_card)
		match m_suit:
			Suit.SPADES:
				_spades_cards.append(m_rank)
			Suit.DIAMONDS:
				_diamonds_cards.append(m_rank)
			Suit.CLUBS:
				_clubs_cards.append(m_rank)
			Suit.HEARTS:
				_hearts_cards.append(m_rank)
	
	_spades_cards.sort_custom(self, "_sort_descending")
	_diamonds_cards.sort_custom(self, "_sort_descending")
	_clubs_cards.sort_custom(self, "_sort_descending")
	_hearts_cards.sort_custom(self, "_sort_descending")
#	print("_suit_cards: %s" % [_suit_cards])


func _on_throw_turn_started(
	_p_game_round: int,
	p_play_turn: int,
	p_throw_turn: int,
	p_throw_player: Player
) -> void:
	if p_throw_turn == 0:
		if p_play_turn == 0:
			_game_starter = p_throw_player
			_create_play_strategies()


func _on_throw_card_selected(
	p_card: String,
	_p_game_round: int,
	_p_play_turn: int,
	p_throw_turn: int,
	p_thrown_by: Player
) -> void:
	if p_thrown_by == _logic_holder:
		_remove_played_card(p_card)
	
	_unrevealed_cards[SUIT_MAP[p_card[-1]]].erase(int(p_card))
	
	if p_throw_turn == 0:
		_suit_play_turns[SUIT_MAP[p_card[-1]]] += 1


func _on_thrown_card_undo(
	p_card: String,
	p_throw_turn: int,
	p_thrown_by: Player
) -> void:
	print("Undo Card: %s, player: %s" % [p_card, p_thrown_by.username])
	if p_thrown_by == _logic_holder:
		_add_undo_card(p_card)
	
	_unrevealed_cards[SUIT_MAP[p_card[-1]]].append(int(p_card))
	
	if p_throw_turn == 0:
		_suit_play_turns[SUIT_MAP[p_card[-1]]] -= 1


func _on_throw_turn_completed(
	p_throw_turn: int,
	p_thrown_by: Player,
	p_match_played_cards: Array
) -> void:
	# if thrown by me, don't update model.
#	if p_thrown_by == _logic_holder:
#		_update_model(Match.played_cards)
#		return

	if p_throw_turn == 3:
#		_update_model(Match.played_cards)
		var m_first_throw_card_suit = SUIT_MAP[p_match_played_cards[0][-1]]
		var m_winning_card_suit = SUIT_MAP[
			_game_helper.get_winning_card(p_match_played_cards)[-1]
		]
		if m_first_throw_card_suit != Suit.SPADES:
			if m_winning_card_suit == Suit.SPADES:
				_cut_trick_played[m_first_throw_card_suit] = 1
		
		# find play turn starter.
		var m_play_turn_starter = _game_helper.get_right_side_player(
			p_thrown_by,
			_match_players
		)
#		print(m_play_turn_starter)
#		print(_logic_holder)
#		print(m_first_throw_card_suit)
#		print()
#		Breakpoint testing -samar
		if _logic_holder != m_play_turn_starter:
			#delete strategy
#			print(_play_strategies)
#			print(m_first_throw_card_suit)
			var m_index = 0
			# Breakpoint here for testing
			for i in _play_strategies:
				var m_strategy = i.keys()[0]
				var m_suit = i.values()[0]
#				print(m_strategy, m_suit)
#				if (m_strategy == "bring_down_ace" and m_suit == m_first_throw_card_suit): -samar strat
				if (
					m_strategy == Strategy.BRING_DOWN_ACE and
					m_suit == m_first_throw_card_suit
				):
					_play_strategies.remove(m_index)
#				if (m_strategy == "bring_down_king" and m_suit == m_first_throw_card_suit): -samar strat
				if (
					m_strategy == Strategy.BRING_DOWN_KING and
					m_suit == m_first_throw_card_suit
				):
					_play_strategies.remove(m_index)
#				if (m_strategy == "compete_spades" and m_suit == m_first_throw_card_suit): -samar strat
				if (
					m_strategy == Strategy.COMPETE_SPADES and
					m_suit == m_first_throw_card_suit
				):
					_play_strategies.remove(m_index)
					break
				m_index += 1
	
		return
	
#	# if _logic_holder is right of throw player.
#	var player_after_throw_player: Player = _game_helper.get_right_side_player(p_thrown_by)
#	if _logic_holder == player_after_throw_player:
#		_update_model(Match.played_cards)


func _on_game_round_completed(_p_round: int) -> void:
	# -samar the parameter is never used
	_clear_bot_logic_data()


func _on_less_than_8_bid_detected() -> void:
	_clear_bot_logic_data()


func _on_player_requested_reshuffle() -> void:
	_clear_bot_logic_data()


func _clear_bot_logic_data():
	_play_strategies.clear()
	_diamonds_cards.clear()
	_hearts_cards.clear()
	_clubs_cards.clear()
	_spades_cards.clear()
	
	for m_suit in _suit_play_turns:
		_suit_play_turns[m_suit] = 0
	
	for m_suit in _cut_trick_played:
		_cut_trick_played[m_suit] = 0


# UNUSED METHOD: method not used might be useful later
#func _setup_model() -> void:
#	var m_template_enemy_card_probs = {
#		Suit.SPADES: {},
#		Suit.HEARTS: {},
#		Suit.CLUBS: {},
#		Suit.DIAMONDS: {},
#	}
#
#	for m_card_id in Deck.cards:
#		if m_card_id in _logic_holder.cards:
#			continue
#		var card = Deck.cards_instances[m_card_id]
#		m_template_enemy_card_probs[card.suit][card.rank] = 1.0/3
#
#	for m_each in _match_players:
#		if m_each == _logic_holder:
#			continue
#
#		_model[m_each] = m_template_enemy_card_probs.duplicate(true)


func _init_unrevealed_cards() -> void:
	_unrevealed_cards = {
		Suit.DIAMONDS: [],
		Suit.CLUBS: [],
		Suit.HEARTS: [],
		Suit.SPADES: []
	}
	for i in range(14, 1, -1):
		_unrevealed_cards[Suit.DIAMONDS].append(i)
		_unrevealed_cards[Suit.CLUBS].append(i)
		_unrevealed_cards[Suit.HEARTS].append(i)
		_unrevealed_cards[Suit.SPADES].append(i)


func _calculate_power_score(p_suit_cards: Array) -> float:
	"""
	Calculating sum of probabilities of winning with power cards.
	For a power card to win:
	Qn. p_what are the chances that all opponents have at least [x] cards ?
	"""
	var m_prob_from_ace: float = 0
	var m_prob_from_king: float = 0
	var m_prob_from_queen: float = 0
	
	# If player has Ace
	if p_suit_cards.has(14):
		if p_suit_cards.size() <= 6:
			m_prob_from_ace = 1.0
			_no_of_cards_evaluated_from_power += 1
		else:
			m_prob_from_ace = _prob_at_least(1, p_suit_cards.size())
			_no_of_cards_evaluated_from_power += 1
	
	# If player has King
	if p_suit_cards.has(13):
		#if i have less than 2 cards then return 0
		if p_suit_cards.size() >= 2:
			m_prob_from_king = _prob_at_least(2, p_suit_cards.size())
			_no_of_cards_evaluated_from_power += 1
		else:
			m_prob_from_king = 0.0
	
	# If player has Queen
	if p_suit_cards.has(12):
		#if i have less than 3 cards then return 0
		if p_suit_cards.size() >= 3:
			m_prob_from_queen = _prob_at_least(3, p_suit_cards.size())
		elif p_suit_cards.has(11) and p_suit_cards.size() == 2:
			m_prob_from_queen = 0.2
		elif p_suit_cards.has(10) and p_suit_cards.size() == 2:
			m_prob_from_queen = 0.1
		else:
			m_prob_from_queen = 0.0
	
#	print("Prob of winning from Ace: ", str(m_prob_from_ace))
#	print("Prob of winning from King: ", str(m_prob_from_king))
#	print("Prob of winning from Queen: ", str(m_prob_from_queen))
#	print("Total Power Score is %s" % str(m_prob_from_ace + m_prob_from_king + m_prob_from_queen))
	return m_prob_from_ace + m_prob_from_king + m_prob_from_queen


func _calculate_cut_score(p_suit_cards: Array) -> float:
	var m_cut_prob: float = 0
	var m_dist: Dictionary = PROB_DIST[p_suit_cards.size()]
	for m_atleast in m_dist.keys():
		if m_atleast <= p_suit_cards.size():
			continue
		if m_dist[m_atleast] != 0:
			m_cut_prob += m_dist[m_atleast]
			if m_atleast != 4:
				_no_of_cards_evaluated_from_cut += 1
			if p_suit_cards.size() == 2 and m_atleast == 3:
				m_cut_prob += 0.2
#			print("Cut Score is: %s" % str(m_dist[m_atleast]))
	
#	print("Total Cut Score is: %s" % str(m_cut_prob))
	return m_cut_prob


func _calculate_spades_score(
	p_my_spades_cards: Array,
	p_total_cut_score: float
) -> Array:
	p_my_spades_cards.sort_custom(self, "_sort_descending")
	var m_total_spades_score: float = 0
	var m_my_remaining_spades := p_my_spades_cards.duplicate()
	var m_opponent_higher_spades: int = 0
	
	for m_card in p_my_spades_cards:
		m_opponent_higher_spades = _get_opponents_total_higher_cards(
			m_card,
			p_my_spades_cards
		)
		
		if m_opponent_higher_spades == 0:
			m_total_spades_score += 1
			m_my_remaining_spades.remove(0)

	var m_confirm_score = m_total_spades_score
	
	#writing simplified method to calculate confirm score
	var opp_remaining_spades:Array = []
	for i in range(14,1,-1):
		if p_my_spades_cards.has(i):
			continue
		opp_remaining_spades.append(i)
	
	for i in range(len(m_my_remaining_spades)):
		if _get_opponents_total_higher_cards_from_remaining(
			m_my_remaining_spades[i],
			p_my_spades_cards, opp_remaining_spades
		) >= 1:
			m_my_remaining_spades[i] = 0
			opp_remaining_spades.remove(0)
	
	m_confirm_score += (
		m_my_remaining_spades.size() - m_my_remaining_spades.count(0)
	)
	_no_of_cards_evaluated_from_spades += m_confirm_score
	
#	print("__________CONFIRM SPADES SCORE FROM NEW CODE________")
#	print("CONFIRM SPADES SCORE: ",confirm_score)
	m_total_spades_score = m_confirm_score
	
	#commented to add another logic
#	var other_spades_with_me = my_spades_cards.size() - m_total_spades_score
#	if other_spades_with_me > 0:
#		var spades_brought_down_maybe = m_total_spades_score * 4
#		var with_opponents_maybe = 13 - spades_brought_down_maybe - other_spades_with_me
#		if other_spades_with_me > with_opponents_maybe / 3.0:
#			if _spades_cards.size() > 5 and confirm_score > 0:
#				m_total_spades_score += (other_spades_with_me - with_opponents_maybe/ 3.0) * 0.75
#				_no_of_cards_evaluated_from_spades += round((other_spades_with_me - with_opponents_maybe/ 3.0) * 0.75)
#
#			else:
#				m_total_spades_score += (other_spades_with_me - with_opponents_maybe/ 3.0) * 0.5
#				_no_of_cards_evaluated_from_spades += round((other_spades_with_me - with_opponents_maybe/ 3.0) * 0.5)
#
#	if round(m_total_spades_score) >= len(_spades_cards) - _spare_spades:
#		m_total_spades_score = max(0, len(_spades_cards) - _spare_spades)
#	print("PROJECTED SPADES SCORE: ", m_total_spades_score)
	
	#another logic using division by 28 rule
#	if total_cut_score + confirm_score < len(_spades_cards) - _spare_spades:
#		for i in range(confirm_score, len(_spades_cards) - int(round(total_cut_score))):
#			m_total_spades_score += _spades_cards[i] / 28.0
#			_no_of_cards_evaluated_from_spades += _spades_cards[i] / 28.0
	
	#another logic using posterior probabilities
	if p_total_cut_score + m_confirm_score < len(_spades_cards) - _spare_spades:
		for i in range(
			m_confirm_score, len(_spades_cards) - int(round(p_total_cut_score))
		):
			m_total_spades_score += SPADE_PROBS[_spades_cards[i]]
			_no_of_cards_evaluated_from_spades += SPADE_PROBS[_spades_cards[i]]
	
	if m_confirm_score == len(_spades_cards):
		m_total_spades_score = m_confirm_score
	
	return [m_confirm_score, m_total_spades_score]


func _get_opponents_total_higher_cards(p_rank: int, p_my_cards: Array) -> int:
	var m_opponent_higher_spades = 0
	for i in range(14, p_rank, -1):
		if p_my_cards.has(i):
#			return m_opponent_higher_spades
			continue
		if i > p_rank:
			m_opponent_higher_spades += 1
	return m_opponent_higher_spades


func _get_opponents_total_higher_cards_from_remaining(
	p_card: int,
	_p_my_spades_cards: Array,
	p_opp_remaining_spades: Array
) -> int:
	# -samar 3rd parameter is never used
	var m_opponent_higher_spades = 0
	for opp_card in p_opp_remaining_spades:
		if opp_card > p_card:
			m_opponent_higher_spades += 1
#	print(typeof(m_opponent_higher_spades))
	return m_opponent_higher_spades


# UNUSED METHOD: method not used - might be useful later
#func _update_model(p_cards_on_floor: Array, p_match_played_cards: Array) -> void:
#	for i in range(len(p_cards_on_floor)):
#		var m_card = Deck.cards_instances[p_cards_on_floor[i]]
#		###Needs optimization
#		for opponent in _match_players:
#			if opponent == _logic_holder:
#				continue
#			if (
#				str(m_card.rank) + _get_suit_letter(m_card.suit) in
#				_initial_cards_set
#			):
#				continue
#			if opponent == m_card.card_owner :
#				_model[opponent][m_card.suit][m_card.rank] = -1.0
#			else:
#				_model[opponent][m_card.suit][m_card.rank] = 0.0
#
##	print("......BEFORE HIGHER CARDS DISCOUNT......")
##	print("Model of: ", self)
##	print(self.model)
#
#	if len(p_cards_on_floor) > 1:
#		var m_card_instances:Array = []
#		for card in p_match_played_cards:
#			m_card_instances.append(Deck.cards_instances[card])
#
#		_discount_higher_cards(m_card_instances)
##	print("......AFTER HIGHER CARDS DISCOUNT......")
##	print("Model of: ",self)
##	print(self.model)
##	print()
##	if len(m_cards_on_floor) == 4:
##		var winning_card = _game_helper.get_winning_card(m_cards_on_floor)
##		var win_rank = Deck.cards_instances[winning_card].rank
##		var win_suit = Deck.cards_instances[winning_card].suit
##		var winning_card_throw_turn := m_cards_on_floor.find(winning_card)
##		if win_suit != CONFIG.Suit.SPADES:
##			for i in range(winning_card_throw_turn + 1, 4):
##				if Deck.cards_instances[m_cards_on_floor[i]].suit == win_suit:
##					if Deck.cards_instances[m_cards_on_floor[i]].rank == win_rank - 1:
##						print(Deck.cards_instances[m_cards_on_floor[i]].card_owner, " has no remaining card of this Suit")
##						print("Or has ", win_rank - 2, " ", _get_suit_letter(win_suit))
##						print()


# UNUSED METHOD: might be useful later
#func _discount_higher_cards(p_card_instances_on_floor: Array) -> void:
#	var m_higher_cards_list:Array = []
#	var m_spades_cards_list:Array = []
#	var m_ranks:Array = []
#	var m_owners:Array = []
#
#	for i in range(len(p_card_instances_on_floor)):
#		var m_card = p_card_instances_on_floor[i]
#		m_ranks.append(m_card.rank)
#		m_owners.append((m_card.card_owner))
#
#	for i in range(len(p_card_instances_on_floor)-1):
#		# var _card = p_card_instances_on_floor[i]
#		m_higher_cards_list = []
#		if p_card_instances_on_floor[i+1].suit != p_card_instances_on_floor[0].suit :
#			if p_card_instances_on_floor[i+1].suit == Suit.SPADES:
#				for values in range(2,15):
#					m_higher_cards_list.append(values)
#
#				_update_higher_cards_prob(
#						m_higher_cards_list,
#						p_card_instances_on_floor[0].suit,
#						m_owners[i+1]
#				)
#
#			else:
#				if p_card_instances_on_floor[0].suit == Suit.SPADES:
#					for values in range(2,15):
#						m_higher_cards_list.append(values)
#
#					_update_higher_cards_prob(
#							m_higher_cards_list,
#							p_card_instances_on_floor[0].suit,
#							m_owners[i+1]
#					)
#
#				else:
#					for values in range(2,15):
#						m_higher_cards_list.append(values)
#
#					_update_higher_cards_prob(
#							m_higher_cards_list,
#							p_card_instances_on_floor[0].suit,
#							m_owners[i+1]
#					)
#
#					m_spades_cards_list = []
#					m_higher_cards_list = []
#
#					for each in p_card_instances_on_floor:
#						if each.suit == Suit.SPADES:
#							m_spades_cards_list.append(each.rank)
#
#					if len(m_spades_cards_list) != 0:
#						for values in range(_max_arr(m_spades_cards_list.slice(0,i+1))+1,15):
#							m_higher_cards_list.append(values)
#
#						_update_higher_cards_prob(
#								m_higher_cards_list,
#								Suit.SPADES,
#								m_owners[i+1]
#						)	
#
#		else:
#			if m_ranks[len(m_ranks.slice(0,i+1))-1] == _max_arr(m_ranks.slice(0,i+1)):
#				pass
#
#			else:
#				for values in range(_max_arr(m_ranks.slice(0,i+1))+1,15):
#					m_higher_cards_list.append(values)
#
#				_update_higher_cards_prob(
#						m_higher_cards_list,
#						p_card_instances_on_floor[0].suit,
#						m_owners[i+1]
#				)


# UNUSED METHOD: might be useful later
#func _update_higher_cards_prob(
#	p_unavailable_higher_ranks: Array,
#	p_suit_id: int, p_opponent: Player
#) -> void:
#	for rank in p_unavailable_higher_ranks:
#		if _logic_holder != p_opponent:
#			if str(rank) + _get_suit_letter(p_suit_id) in _initial_cards_set:
#				continue
#
#			if _model[p_opponent][p_suit_id][rank] > 0.0:
#				var m_this_opponent_prob = _model[p_opponent][p_suit_id][rank]
#				_model[p_opponent][p_suit_id][rank] = 0.0	
##				print("updated:", opponent, " ", _get_suit_letter(suit_id), rank)
#
#				var m_other_2_players:Array = []
#				for player in _match_players:
#					if player != _logic_holder and player != p_opponent:
#						m_other_2_players.append(player)
#
#				var second_opponent_prob = _model[m_other_2_players[0]][p_suit_id][rank] 
#				var third_opponent_prob = _model[m_other_2_players[1]][p_suit_id][rank]
#
#				if second_opponent_prob > 0 and third_opponent_prob <= 0:
#					_model[m_other_2_players[0]][p_suit_id][rank] = 1.0
#
#				elif third_opponent_prob > 0 and second_opponent_prob <= 0:
#					_model[m_other_2_players[1]][p_suit_id][rank] = 1.0
#
#				elif second_opponent_prob > 0 and third_opponent_prob > 0:
#					_model[m_other_2_players[0]][p_suit_id][rank] += m_this_opponent_prob/2.0
#					_model[m_other_2_players[1]][p_suit_id][rank] += m_this_opponent_prob/2.0


func _max_arr(p_arr: Array) -> int:
	assert(!p_arr.empty())
	return p_arr.max()


func _min_arr(p_arr: Array) -> int:
	assert(!p_arr.empty())
	return p_arr.min()


func _get_suit_letter(p_suit_id: int) -> String:
	if p_suit_id == Suit.DIAMONDS:
		return "D"
	if p_suit_id == Suit.CLUBS:
		return "C"
	if p_suit_id == Suit.HEARTS:
		return "H"
	if p_suit_id == Suit.SPADES:
		return "S"
	
	return ""


func _is_all_spades_in_legal_cards() -> bool:
	for m_card in _logic_holder.legal_cards:
		if SUIT_MAP[m_card[-1]] != Suit.SPADES:
			return false
	return true


func _get_random_card(p_cards: Array) -> String:
#	print_debug("-> Getting a random m_card.")
	var m_card = p_cards[randi() % p_cards.size()]
	var suit_id = SUIT_MAP[m_card[-1]]
	var rand_gen_count = 0
	if _unrevealed_cards[suit_id].size() == 1:
#		print("___RETURNED____: ", m_card)
		return m_card
		
#	while _get_senior_card(suit_id) == _get_largest_of_mycards_from_this_suit(suit_id):
	while _unrevealed_cards[suit_id][1] == int(m_card):
		if p_cards.size() == 1:
			break
		if rand_gen_count >= 20:
			break
		m_card = p_cards[randi() % p_cards.size()]
		suit_id = SUIT_MAP[m_card[-1]]
		rand_gen_count += 1
		if _unrevealed_cards[suit_id].size() == 1:
#			print("___RETURNED____: ", m_card)
			return m_card

#	print(_diamonds_cards)
#	print(_clubs_cards)
#	print(_hearts_cards)
	return m_card


func _get_largest_of_mycards_from_this_suit(p_suit_id: int) -> int:
	assert(!_logic_holder.legal_cards.empty())
	var m_card_ranks = []
	for card in _logic_holder.cards:
		if SUIT_MAP[card[-1]] != p_suit_id:
			continue
		
		m_card_ranks.append(int(card))
	assert(!m_card_ranks.empty())
	return _max_arr(m_card_ranks)


func _get_smallest_from_legal_cards(_p_match_played_cards: Array) -> String:
	var m_legal_cards_ranks:Array = []
	
	# here _logic_holder.legal_cards can be null
	# in such case the _min_arr(null) will return "Null"
	if _is_all_spades_in_legal_cards():
		for card in _logic_holder.legal_cards:
			m_legal_cards_ranks.append(int(card))
	else:
		for card in _logic_holder.legal_cards:
			if SUIT_MAP[card[-1]] != Suit.SPADES:
				m_legal_cards_ranks.append(int(card))

	return str(_min_arr(m_legal_cards_ranks))


func _is_there_a_cut_trick(p_match_played_cards: Array) -> bool:
	var m_played_card_1: String = p_match_played_cards[1]
	var m_played_card_2: String = p_match_played_cards[2]
	
	if (
		SUIT_MAP[m_played_card_1[-1]] == Suit.SPADES ||
		SUIT_MAP[m_played_card_2[-1]] == Suit.SPADES
	):
		return true
	return false


func _create_play_strategies() -> void:
#	if self.play_strategies.size() == 0:
#	for suit in range(3): -samar
	for suit in Suit.values():
		if (suit == Suit.SPADES):
			continue
		if _suit_cards[suit].size() != 0 and _suit_play_turns[suit] == 0:
			if !_is_suit_has_ace(_suit_cards[suit]):
				if _is_suit_has_king(_suit_cards[suit]):
					if !_is_suit_has_queen(_suit_cards[suit]):
						if len(_suit_cards[suit]) > 2:
#							_play_strategies.append({"bring_down_ace": suit}) -samar strat
							_play_strategies.append({Strategy.BRING_DOWN_ACE: suit})
						if len(_suit_cards[suit]) == 2:
							#can small cards bring down ace or king?? discuss
							if _get_2nd_largest(_suit_cards[suit]) >=7:
#								_play_strategies.append({"bring_down_ace": suit}) -samar strat
								_play_strategies.append({Strategy.BRING_DOWN_ACE: suit})
							else:
#								_play_strategies.append({"prepare_for_cut": suit}) -samar strat
								_play_strategies.append({Strategy.PREPARE_FOR_CUT: suit})
				elif _is_suit_has_queen(_suit_cards[suit]):
					if len(_suit_cards[suit]) >=2:
#						_play_strategies.append({"bring_down_king": suit}) -samar strat
						_play_strategies.append({Strategy.BRING_DOWN_KING: suit})
					else:
#						_play_strategies.append({"prepare_for_cut": suit}) -samar strat
						_play_strategies.append({Strategy.PREPARE_FOR_CUT: suit})
				elif len(_suit_cards[suit]) <= 2:
#					_play_strategies.append({"prepare_for_cut": suit}) -samar strat
					_play_strategies.append({Strategy.PREPARE_FOR_CUT: suit})
	
	if _suit_play_turns[Suit.SPADES] == 0 and _unrevealed_cards[Suit.SPADES].size() == 13:
		if self._spades_cards.has(14) and self._spades_cards.has(13):
#			_play_strategies.append({"compete_spades": CONFIG.Suit.SPADES}) -samar strat
			_play_strategies.append({Strategy.COMPETE_SPADES: Suit.SPADES})
			if self._spades_cards.has(12):
#				_play_strategies.append({"compete_spades": CONFIG.Suit.SPADES}) -samar strat
				_play_strategies.append({Strategy.COMPETE_SPADES: Suit.SPADES})
				if self._spades_cards.has(11):
#					_play_strategies.append({"compete_spades": CONFIG.Suit.SPADES}) -samar strat
					_play_strategies.append({Strategy.COMPETE_SPADES: Suit.SPADES})
			return
		
		if self._spades_cards.size() - _total_from_spades >= _spare_spades:
			if _logic_holder.bid < 6:
				for _i in range(
					self._spades_cards.size() - _total_from_spades - int(round(_spare_spades))
				):
#					_play_strategies.append({"compete_spades": CONFIG.Suit.SPADES}) -samar strat
					_play_strategies.append({Strategy.COMPETE_SPADES: Suit.SPADES})
			elif self._spades_cards.size() - _total_from_spades >= _spare_spades:
				for _i in range(
					self._spades_cards.size() - _total_from_spades - int(round(_spare_spades))
				):
#					_play_strategies.append({"compete_spades": CONFIG.Suit.SPADES}) -samar strat
					_play_strategies.append({Strategy.COMPETE_SPADES: Suit.SPADES})


func _remove_played_card(p_card: String) -> void:
	var m_rank: int = int(p_card)
	var suit: int = SUIT_MAP[p_card[-1]]
	
	if suit == Suit.DIAMONDS:
		assert(_diamonds_cards.has(m_rank))
		_diamonds_cards.erase(m_rank)
	
	elif suit == Suit.CLUBS:
		assert(_clubs_cards.has(m_rank))
		_clubs_cards.erase(m_rank)
	
	elif suit == Suit.HEARTS:
		assert(_hearts_cards.has(m_rank))
		_hearts_cards.erase(m_rank)
	
	elif suit == Suit.SPADES:
		assert(_spades_cards.has(m_rank))
		_spades_cards.erase(m_rank)
	
#	print("%s removes %s." % [_logic_holder.username, m_card_obj])
#	print("%s: %s" % [CONFIG.suits_unicode[CONFIG.Suit.DIAMONDS], _diamonds_cards])
#	print("%s: %s" % [CONFIG.suits_unicode[CONFIG.Suit.CLUBS], _clubs_cards])
#	print("%s: %s" % [CONFIG.suits_unicode[CONFIG.Suit.HEARTS], _hearts_cards])
#	print("%s: %s" % [CONFIG.suits_unicode[CONFIG.Suit.SPADES], _spades_cards])


func _add_undo_card(p_card: String) -> void:
	var m_rank: int = int(p_card)
	var suit: int = SUIT_MAP[p_card[-1]]
	
	match suit:
		Suit.DIAMONDS:
			if m_rank in _diamonds_cards:
				return
			_diamonds_cards.append(m_rank)
		Suit.HEARTS:
			if m_rank in _hearts_cards:
				return
			_hearts_cards.append(m_rank)
		Suit.CLUBS:
			if m_rank in _clubs_cards:
				return
			_clubs_cards.append(m_rank)
		Suit.SPADES:
			if m_rank in _spades_cards:
				return
			_spades_cards.append(m_rank)


func _get_play_strategy_card(p_play_strategy: Dictionary) -> String:
	var m_suit_cards:Array = []
	for key in p_play_strategy.keys():
		if p_play_strategy[key] == Suit.DIAMONDS:
			m_suit_cards = _diamonds_cards
		if p_play_strategy[key] == Suit.CLUBS:
			m_suit_cards = _clubs_cards
		if p_play_strategy[key] == Suit.HEARTS:
			m_suit_cards = _hearts_cards
		
#		if key == "bring_down_ace": -samar strat
		if key == Strategy.BRING_DOWN_ACE:
				return (
					str(_bring_down_ace(m_suit_cards, p_play_strategy[key]))
					+ _get_suit_letter(p_play_strategy[key])
				)
#		if key == "bring_down_king": -samar strat
		if key == Strategy.BRING_DOWN_KING:
			return (
				str(_bring_down_king(m_suit_cards, p_play_strategy[key]))
				+ _get_suit_letter(p_play_strategy[key])
			)
#		if key == "prepare_for_cut": -samar strat
		if key == Strategy.PREPARE_FOR_CUT:
			return (
				str(_prepare_for_cut(m_suit_cards, p_play_strategy[key]))
				+ _get_suit_letter(p_play_strategy[key])
			)
#		if key == "compete_spades": -samar strat
		if key == Strategy.COMPETE_SPADES:
			return str(_compete_spades()) + _get_suit_letter(p_play_strategy[key])

	return ""


func _bring_down_ace(p_suit_cards: Array, p_suit: int) -> int:
	if p_suit_cards.has(_get_senior_card(p_suit)) and _cut_trick_played[p_suit] == 0:
		return _get_senior_card(p_suit)
	else:
		return _get_2nd_largest(p_suit_cards)


func _bring_down_king(p_suit_cards: Array, p_suit: int) -> int:
	if p_suit_cards.has(_get_senior_card(p_suit)) and _cut_trick_played[p_suit] == 0:
		return _get_senior_card(p_suit)
	else:
		return _get_2nd_largest(p_suit_cards)


func _prepare_for_cut(p_suit_cards: Array, p_suit: int) -> int:
	if p_suit_cards.has(_get_senior_card(p_suit)):
		return _get_senior_card(p_suit)
	else:
		return _get_2nd_largest(p_suit_cards)
#		return suit_cards[randi() % suit_cards.size()]


func _compete_spades() -> int:
	if (
		self._spades_cards.size() > 1 and
		self._spades_cards.has(_get_senior_card(Suit.SPADES)) and
		self._spades_cards.has(_unrevealed_cards[Suit.SPADES][1])
	):
#	if self._spades_cards.has(_get_senior_card(CONFIG.Suit.SPADES)) and self._spades_cards.has(_get_senior_card(CONFIG.Suit.SPADES)-1):
		return _get_senior_card(Suit.SPADES)
	if (
		self._spades_cards.size() > 1 and
		self._spades_cards.has(_get_senior_card(Suit.SPADES)) and
		(
			_unrevealed_cards[Suit.SPADES].size() - self._spades_cards.size()
			<= self._spades_cards.size()
		)
	):
		return _get_senior_card(Suit.SPADES)
	elif (
		self._spades_cards.has(10) and
		_get_largest_of_mycards_from_this_suit(Suit.SPADES) != 10
	):
		return 10
	elif (
		self._spades_cards.has(9) and
		_get_largest_of_mycards_from_this_suit(Suit.SPADES) != 9
	):
		return 9
	else:
		return _min_arr(self._spades_cards)


func _get_2nd_largest(p_arr: Array) -> int:
	if (p_arr.size() < 2):
		return p_arr[0]
	p_arr.sort()
	return p_arr[p_arr.size() - 2]


func _is_suit_has_king(p_suit_cards: Array) -> bool:
	return p_suit_cards.has(13)


func _is_suit_has_ace(p_suit_cards: Array) -> bool:
	return p_suit_cards.has(14)


func _is_suit_has_queen(p_suit_cards: Array) -> bool:
	return p_suit_cards.has(12)


func _get_senior_card(p_suit_id: int) -> int:
	if _unrevealed_cards[p_suit_id].size() > 0 :
		return _unrevealed_cards[p_suit_id][0]
	return 0
