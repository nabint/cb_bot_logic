# ------------------
# Base class for all bot logics.
# Every different kind of bot logic will extend this class.
# ------------------
class_name BotLogic
extends Node

# Outer key = My total cards
# Inner key = All opponent should have at least
const PROB_DIST: Dictionary = {
	0: {
		1: 0.995883,
		2: 0.949609,
		3: 0.727636,
		4: 0.246271,
		5: 0,
	},
	1: {
		1: 0.992759,
		2: 0.915651,
		3: 0.603479,
		4: 0.093717,
		5: 0,
	},
	2: {
		1: 0.986198,
		2: 0.862628,
		3: 0.450359,
		4: 0,
	},
	3: {
		1: 0.974804,
		2: 0.783795,
		3: 0.27687,
		4: 0,
	},
	4: {
		1: 0.956055,
		2: 0.672427,
		3: 0.110936,
		4: 0,
	},
	5: {
		1: 0.924156,
		2: 0.524086,
		3: 0,
	},
	6: {
		1: 0.872706,
		2: 0.339533,
		3: 0,
	},
	7: {
		1: 0.790307,
		2: 0.145071,
		3: 0,
	},
	8: {
		1: 0.668897,
		2: 0,
	},
	9: {
		1: 0.504717,
		2: 0,
	},
	10: {
		1: 0.2,
		2: 0,
	},
	11: {
		1: 0,
		2: 0,
	},
	12: {
		1: 0,
		2: 0,
	},
	13: {
		1: 0,
		2: 0,
	}
}


var _logic_holder: Player


func _init(p_logic_holder: Player) -> void:
	_logic_holder = p_logic_holder


func select_bid_amount(
	p_match_info_object: Match.MatchInfoBot,
	_p_g_total_scores: Dictionary
) -> int:
	return -1


func select_throw_card(
	_p_match_current_throw_turn: int,
	_p_match_dealer: Player,
	_p_match_played_cards: Array
) -> String:
	return ""


# Probability of all opponents having at least this many cards
func _prob_at_least(p_opp_at_least: int, p_my_total_cards: int) -> float:
	assert(p_opp_at_least >= 0 && p_opp_at_least <= 13, "Should be between 0 and 13.")
	assert(p_my_total_cards >= 0 && p_my_total_cards <= 13, "Should be between 0 and 13.")
	if p_opp_at_least == 0:
		return 1.0
	
	if PROB_DIST.has(p_my_total_cards):
		var m_dist: Dictionary = PROB_DIST[p_my_total_cards]
		if m_dist.has(p_opp_at_least):
			return float(m_dist[p_opp_at_least])
		else:
			return 0.0
	else:
		return 0.0


func _get_sorted_cards(p_spades: Array, p_diamonds: Array, p_clubs: Array, p_hearts: Array) -> Array:
	var m_sort_func: String = "_sort_descending"
	p_spades.sort_custom(self, m_sort_func)
	p_diamonds.sort_custom(self, m_sort_func)
	p_clubs.sort_custom(self, m_sort_func)
	p_hearts.sort_custom(self, m_sort_func)
	
	var m_sorted_cards: Array = []
	
	for m_rank in p_spades:
		m_sorted_cards.append(_get_card(m_rank, CONFIG.Suit.SPADES, true))
	
	for m_rank in p_diamonds:
		m_sorted_cards.append(_get_card(m_rank, CONFIG.Suit.DIAMONDS, true))
	
	for m_rank in p_clubs:
		m_sorted_cards.append(_get_card(m_rank, CONFIG.Suit.CLUBS, true))
	
	for m_rank in p_hearts:
		m_sorted_cards.append(_get_card(m_rank, CONFIG.Suit.HEARTS, true))
	
	return m_sorted_cards


func _get_card(p_rank: int, p_suit: int, p_is_beautify: bool = false) -> String:
	var m_suit_str: String = CONFIG.suits_unicode[p_suit] if p_is_beautify else CONFIG.suits_str[p_suit]
	return str(p_rank) + m_suit_str


func _sort_descending(p_a, p_b) -> bool:
	if p_a > p_b:
		return true
	return false
