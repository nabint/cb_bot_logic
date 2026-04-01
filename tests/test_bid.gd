# test_bot_logic.gd
extends SceneTree


enum GameMode {
	STANDARD = 1,
	QUICK,
	EIGHT_BID_CALL,
	EIGHT_BID_BREAK,
	TRAINING,
	TUTORIAL,
	ONE_ROUND
}

var mock_player = load("mocks/mock_player.gd")
var mock_game_helper = load("mocks/mock_game_helper.gd")
var bot_logic = load("bot_logic_prob.gd")
var match_info_bot = load("mocks/mock_match_info_bot.gd")

const DEFAULT_PLAYERS = [
	{"username": "Davy Jones", "in_game_id": 1},
	{"username": "Captain Flint", "in_game_id": 2},
	{"username": "Anne Bonny", "in_game_id": 3},
	{"username": "Jack Rackham", "in_game_id": 4}
]

const DEFAULT_CONFIG = {
	"grand_total_score": {1: 3.1, 2: 4.0, 3: 10.4, 4: 11},
	"game_mode": GameMode.STANDARD,
	"current_game_round": 1,
	"dealer_in_game_id": 1,
	"player_to_bid_ingame_id": 1
}


func _init():
	main()
	quit()


func make_test(cards: Array, expected: int = -1, config_override: Dictionary = {}) -> Dictionary:
	var test = {
		"cards": cards,
		"player_details": DEFAULT_PLAYERS,
		"expected": expected
	}
	
	for key in DEFAULT_CONFIG:
		test[key] = config_override.get(key, DEFAULT_CONFIG[key])
	return test



func sort_cards(cards_string: String) -> Array:
	"""
	Takes a string of card strings (e.g., "[13C, 12H, 2H, 10C]") and returns an array sorted by suit and rank.
	Suit order: S (Spades), H (Hearts), C (Clubs), D (Diamonds)
	Within each suit, cards are sorted by rank in descending order.
	"""
	var suit_order = {"S": 0, "H": 1, "C": 2, "D": 3}
	
	# Remove brackets and split by comma
	var cleaned = cards_string.replace("[", "").replace("]", "").strip_edges()
	var card_array = cleaned.split(",")
	
	# Parse each card into [rank, suit, original_card]
	var parsed_cards = []
	for card in card_array:
		var trimmed_card = card.strip_edges()
		if trimmed_card.empty():
			continue
			
		var suit = trimmed_card[trimmed_card.length() - 1]
		var rank_str = trimmed_card.substr(0, trimmed_card.length() - 1)
		var rank = int(rank_str)
		parsed_cards.append([rank, suit, trimmed_card])
	
	# Custom sort function
	parsed_cards.sort_custom(self, "_compare_cards")
	
	# Extract just the card strings
	var result = []
	for parsed in parsed_cards:
		result.append(parsed[2])
	
	return result

func _compare_cards(a: Array, b: Array) -> bool:
	"""
	Compare function for sorting cards.
	a and b are arrays of [rank, suit, card_string]
	Returns true if a should come before b.
	"""
	var suit_order = {"S": 0, "H": 1, "C": 2, "D": 3}
	
	# First compare by suit
	if suit_order[a[1]] != suit_order[b[1]]:
		return suit_order[a[1]] < suit_order[b[1]]
	
	# If same suit, sort by rank descending (higher ranks first)
	return a[0] > b[0]


var cv_tests: Array = [
	# make_test(sort_cards("[13C, 12H, 2H, 10C, 4D, 9D, 8H, 5C, 7S, 12S, 4S, 10H, 3D]"), 2),
	# make_test(sort_cards("[2D, 6S, 3D, 7S, 4C, 14C, 4D, 14S, 10S, 11H, 4S, 8S, 9S]"), 2),
	# make_test(sort_cards("[3C, 12H, 3H, 11D, 5C, 3S, 2S, 7S, 13H, 11C, 10S, 6D, 5D]"), 1),
	# make_test(sort_cards("[13D, 2C, 14S, 9C, 13C, 9D, 13H, 10S, 6C, 5H, 12S, 13S, 11S]"), 1),

	# make_test(sort_cards("[9C, 8C, 12S, 14S, 8H, 14C, 14H, 4D, 3D, 12H, 6C, 14D, 8D]"), 4),
	# make_test(sort_cards("[10S, 14S, 4H, 3S, 11D, 8C, 13C, 11S, 7S, 8D, 13H, 14C, 2S]"), 0),

	# make_test(sort_cards("[7D, 12H, 13D, 2H, 3H, 6D, 14H, 4C, 9H, 3D, 13C, 5C, 11H]"), 0),

	make_test(sort_cards("[6D, 11C,	2D,	2H,	6S,	11S,	5S,	14S,	4C,	14C,	12C,	13C,	11D]"), 0),

]


func main() -> void:
	var tests: Array = [
		# Format: make_test([cards], expected_bid)
		# make_test(["13S", "5S", "10H", "5H", "2H", "14C", "10C", "6C", "4C", "3C", "14D", "12D", "8D"], 3),
		# make_test(["11S", "10S", "7S", "10H", "7H", "6H", "14C", "10C", "6C", "14D", "10D", "6D", "2D"], 3),
		# make_test(["14S", "10S", "8S", "6S", "13H", "6H", "3H", "11C", "4C", "3C", "2C", "11D", "10D"], 3),
		# make_test(["12S", "6S", "2S", "13H", "11H", "10H", "9H", "8H", "5H", "12C", "11C", "10C", "9D"], 2),
		# make_test(["14S", "11S", "8S", "11H", "9H", "4H", "12C", "8C", "5C", "2C", "13D", "11D", "5D"], 2),
		# make_test(["13S", "10S", "9S", "2S", "7H", "6H", "3H", "14C", "8C", "4C", "3C", "8D", "7D"], 3),
		# make_test(["10S", "8S", "6S", "12H", "11H", "10H", "9H", "4H", "3H", "8C", "7C", "5C", "2C"], 1),

		# make_test(["7S", "3S", "14H", "13H", "8H", "7H", "12C", "3C", "12D", "11D", "9D", "6D", "2D"], 2),
		# make_test(["10S", "7S", "6S", "13H", "4H", "3H", "2H", "14C", "5C", "4C", "13D", "8D", "4D"], 3),
		# make_test(["12S", "10S", "10H", "7H", "6H", "5H", "13C", "6C", "4C", "12D", "10D", "5D", "3D"], 1),
		# make_test(["11S", "10S", "10H", "8H", "7H", "8C", "7C", "5C", "14D", "12D", "8D", "7D", "5D"], 1),
		# make_test(["10S", "8S", "6S", "13H", "12H", "8H", "6H", "4H", "9C", "8C", "4C", "6D", "3D"], 1),
		# make_test(["13S", "9S", "8S", "6S", "5S", "7H", "6H", "3H", "14C", "11C", "10C", "10D", "7D"], 3),
		# make_test(["10S", "5S", "4S", "2S", "10H", "7H", "2H", "14C", "11C", "9C", "7C", "6C", "6D"], 2),
		# make_test(["14S", "8S", "2S", "12H", "8H", "14C", "10C", "7C", "5C", "2C", "7D", "3D", "2D"], 2),
		# make_test(["12S", "7S", "10H", "7H", "6H", "2H", "14C", "11C", "9C", "8C", "8D", "7D", "4D"], 1),
		# make_test(["9S", "7S", "13H", "6H", "11C", "10C", "8C", "7C", "5C", "13D", "12D", "6D", "5D"], 2),
		# make_test(["8S", "5S", "4S", "13H", "12H", "10H", "7H", "3H", "2H", "9C", "5C", "2C", "11D"], 1),
		
		# # # Spade dominance tests (many spades)
		# make_test(["14S", "13S", "12S", "10S", "5S", "3S", "4S", "12C", "7C", "2C", "13D", "8D", "3D"], -1),
		# make_test(["9S", "7S", "6S", "5S", "4S", "2S", "14C", "13C", "6C", "14D", "12D", "9D", "3D"], -1),
		# make_test(["13S", "12S", "10S", "9S", "5S", "4S", "13H", "12H", "7H", "13C", "11C", "8C", "3C"], -1),
		# make_test(["13S", "12S", "8S", "7S", "6S", "4S", "8H", "9C", "5C", "4C", "2C", "13D", "12D"], -1),
		# make_test(["12S", "11S", "7S", "6S", "4S", "3S", "14H", "10H", "14C", "7C", "5C", "3C", "14D"], -1),
		# make_test(["14S", "10S", "9S", "7S", "3S", "13H", "7H", "3H", "14C", "13C", "13D", "12D", "5D"], -1),

		# make_test(["13S", "5S", "9H", "7H", "4H", "13C", "9C", "5C", "3C", "13D", "9D", "8D", "3D"], 3),
		# make_test(["13S", "11S", "7S", "4S", "2S", "11H", "7H", "4H", "3H", "13D", "10D", "3D", "2D"], 4), # Got 1 in a case R3 T2
		# make_test(["13S", "7S", "14H", "10H", "9H", "6H", "4H", "2H", "14C", "8C", "5C", "13D", "3D"], 4), # Got 1 in a case R3 T2
		# make_test(["14S", "13S", "12S", "4S", "9H", "7H", "6H", "14C", "6C", "14D", "10D", "9D", "3D"], 5), # Got 6 in a case R3 T2
		# make_test(["9S", "6S", "14H", "12H", "11H", "9H", "8H", "12C", "8C", "6C", "12D", "7D", "6D"], 1), # Look into this should get 1
	
		# make_test(["13S", "10S", "2S", "11H", "8H", "5H", "13C", "8C", "4C", "3C", "14D", "12D", "9D"], 3), # Look into this should get 1
		# make_test(["14S", "13S", "10S", "8S", "10H", "3H", "9C", "6C", "4C", "3C", "2C", "12D", "11D"], 3), # Look into this should get 1
		# make_test(["11S", "2S", "9H", "7H", "3H", "11C", "8C", "6C", "14D", "13D", "8D", "6D", "4D"], 1), # Look into this should get 1
		# make_test(["6S", "5S", "14H", "13H", "11H", "8H", "14C", "11C", "10C", "9C", "4C", "5D", "2D"], 3), # Look into this should get 1
		# make_test(["13S", "3S", "2S", "12H", "9H", "3H", "13C", "11C", "2C", "14D", "12D", "10D", "6D"], 3), # Look into this should get 1
		# make_test(["12S", "10S", "8S", "6S", "2S", "7H", "6H", "4H", "2H", "5C", "13D", "6D", "3D"], 4), # Look into this should get 1
		# make_test(["8S", "7S", "5S", "2S", "12H", "11H", "8H", "7H", "4H", "13D", "11D", "8D", "6D"], 2), # Look into this should get 1
		# make_test(["11S", "10S", "8S", "13H", "11H", "4H", "2H", "11C", "5C", "2C", "14D", "7D", "4D"], 3), # Look into this should get 1
		# make_test(["14S", "8S", "7S", "2S", "14H", "12H", "9H", "6H", "4H", "3C", "9D", "4D", "2D"], 3), # Look into this should get 1
		# make_test(["7S", "5S", "3S", "11H", "9H", "14C", "8C", "5C", "14D", "12D", "10D", "7D", "4D"], 2), # Look into this should get 1
		# make_test(["13S", "6S", "3S", "13H", "10H", "9H", "13C", "4C", "3C", "13D", "12D", "10D", "3D"], 4), # On the last turn others bid high, but should bid minimum 3 gives 2
		# make_test(["11S", "4S", "2S", "12H", "11H", "6H", "9C", "5C", "14D", "13D", "9D", "6D", "5D"], 2),
		# make_test(["8S", "7S", "2S", "14H", "5H", "13C", "12C", "9C", "5C", "8D", "6D", "5D", "4D"], 2),
		# make_test(["13S", "12S", "6S", "10H", "7H", "6H", "3H", "7C", "3C", "14D", "11D", "7D", "6D"], 3), # 2 larger spades
		# make_test(["13S", "12S", "11S", "3S", "14H", "12H", "6H", "2H", "9C", "4C", "9D", "6D", "4D"], 4), # 2 larger spades
		# make_test(["8S", "7S", "4S", "12H", "11H", "9H", "12C", "7C", "6C", "14D", "11D", "4D", "3D"], 1), # 2 larger spades
		# make_test(["14S", "12S", "9S", "5S", "12H", "10H", "8H", "10C", "5C", "4C", "14D", "13D", "4D"], 4), # 2 larger spades
		# make_test(["13S", "11S", "10S", "4S", "3S", "8H", "7H", "12C", "11C", "8C", "4D", "3D", "2D"], 3), # 2 larger spades
		# make_test(["13S", "5S", "2S", "14H", "7H", "6H", "4H", "2H", "14C", "9C", "14D", "9D", "8D"], 4), # 2 larger spades
		# make_test(["10S", "5S", "4S", "2S", "14H", "11H", "4H", "3H", "2H", "11C", "6C", "4C", "4D"], 2), # 2 larger spades
		# make_test(["13S", "12S", "6S", "2H", "10C", "3C", "2C", "12D", "10D", "8D", "6D", "3D", "2D"], 2), # 2 larger spades
		# make_test(["14S", "10S", "8S", "2S", "7H", "4H", "2H", "13C", "12C", "9C", "9D", "8D", "6D"], 2), # 2 larger spades
		# make_test(["12S", "11S", "8S", "4S", "2S", "14H", "5H", "14C", "12C", "11C", "7C", "12D", "8D"], 4), # 2 larger spades
		# make_test(["14S", "13S", "8S", "4S", "13H", "12H", "8H", "7H", "12C", "10C", "4C", "12D", "4D"], 4), # 2 larger spades
		# make_test(["12S", "7S", "5S", "4S", "3S", "10H", "9H", "2H", "12C", "7C", "3C", "5D", "3D"], 2), # 2 larger spades
		# make_test(["14S", "6S", "3S", "2S", "13H", "11H", "8H", "13C", "10C", "9C", "2C", "13D", "3D"], 4), # 2 larger spades
		# make_test(["11S", "10S", "8S", "7S", "6S", "11H", "2H", "14C", "12C", "6C", "5C", "14D", "9D"], 5), # 2 larger spades
		# make_test(["13S", "12S", "6S", "3H", "2H", "12C", "8C", "7C", "6C", "3C", "13D", "8D", "7D"], 3), # 2 larger spades
		# make_test(["13S", "11S", "6S", "3H", "2H", "12C", "8C", "7C", "6C", "3C", "13D", "8D", "7D"], 2), # 2 larger spades
		# make_test(["10S", "9S", "8S", "7S", "5S", "2S", "13H", "6H", "5H", "12C", "6C", "10D", "2D"], 4), # Large volume of spades but low bid
		# make_test(["14S", "13S", "7S", "5S", "4S", "2S", "13H", "7H", "6H", "3H", "13D", "12D", "3D"], 6), # Large volume of spades but low bid
		# make_test(["12S", "10S", "7H", "6H", "4H", "13C", "9C", "7C", "6C", "14D", "12D", "6D", "3D"], 2), # Large volume of spades but low bid
		# make_test(["14S", "10S", "7S", "6S", "6H", "3H", "2H", "14C", "11C", "13D", "12D", "7D", "5D"],4), # Large volume of spades but low bid
		# make_test(["13S", "12S", "11S", "6S", "2S", "14H", "13H", "7H", "12C", "8C", "2C", "13D", "7D"], 6), # Large volume of spades but low bid
		# make_test(["14S", "13S", "12S", "9S", "8S", "7S", "2S", "12H", "9H", "2H", "4C", "3C", "12D"], 7), # Large volume of spades but low bid
		# make_test(["8S", "7S", "6S", "4S", "2S", "13H", "9H", "8H", "6H", "2H", "13C", "12C", "4C"], 4), # Large volume of spades but low bid
		# make_test(["12S", "10S", "9S", "8S", "13H", "12H", "3H", "14C", "5C", "2C", "14D", "7D", "5D"], 5), # Large volume of spades but low bid
		# make_test(["13S", "11S", "10S", "13H", "12H", "2H", "13C", "11C", "7C", "4C", "14D", "8D", "3D"], 5), # Large volume of spades but low bid
		# make_test(["10S", "8S", "7S", "13H", "12H", "6H", "13C", "12C", "11C", "6C", "11D", "8D", "7D"], 2), # Fix this
		# make_test(["13S", "4S", "2S", "14H", "11H", "2H", "10C", "9C", "8C", "7C", "5D", "4D", "3D"], 2), # Large volume of spades but low bid
		# make_test(["13S", "12S", "6S", "4S", "2S", "9H", "7H", "12C", "7C", "5C", "11D", "9D", "8D"], 3), # Look into this
		# make_test(["13S", "12S", "10S", "6S", "14H", "2H", "11C", "8C", "7C", "2C", "13D", "9D", "3D"], 4), # Look into this
		# make_test(["13S", "5S", "4S", "10H", "5H", "14C", "13C", "12C", "10C", "8C", "4C", "2C", "4D"], 2), # Look into this
		# make_test(["12S", "11S", "8S", "7S", "12H", "11H", "10H", "4H", "3H", "10C", "9C", "3C", "12D"], 2), # Look into this

		# make_test(["11S", "6S", "5S", "2S", "14H", "12H", "8H", "14C", "13D", "10D", "8D", "7D", "4D"], 4), # Look into this
		# make_test(["13S", "11S", "6S", "5S", "4S", "3S", "2S", "12H", "2H", "14C", "13C", "4D", "3D"], 6), # Look into this
		# make_test(["14S", "13S", "11S", "8S", "7S", "3S", "12H", "9H", "11C", "4C", "11D", "7D", "3D"], 4), # Look into this
		# make_test(["13S", "12S", "10S", "9S", "6S", "2S", "8H", "5H", "7C", "5C", "4C", "10D", "4D"], 4), # Look into this
		# make_test(["12S", "8S", "7S", "3S", "2S", "12H", "4H", "2H", "6C", "5C", "11D", "10D", "6D"], 2), # Look into this
		# make_test(["14S", "11S", "8S", "5S", "4S", "3S", "10H", "13C", "12C", "9C", "4C", "5D", "3D"], 5), # Look into this
		# make_test(["14S", "9S", "6S", "3S", "2S", "12H", "4H", "11C", "7C", "14D", "7D", "4D", "3D"], 4), # Look into this
		# make_test(["14S", "13S", "9S", "8S", "4S", "2S", "12H", "10C", "6C", "13D", "9D", "8D", "3D"], 5), # Look into this
		# make_test(["10S", "6S", "4S", "3S", "2S", "14H", "11H", "8H", "12C", "11C", "4C", "11D", "7D"], 3), # Look into this
		# make_test(["14S", "13S", "12S", "7S", "4S", "2S", "12H", "11H", "6H", "2H", "13C", "4C", "2C"], 6), # Look into this
		# make_test(["11S", "13S", "12S", "7S", "4S", "2S", "12H", "11H", "6H", "2H", "13C", "4C", "2C"], 6), # Look into this
		# make_test(["14S", "13S", "10S", "7S", "4S", "2S", "12H", "11H", "6H", "2H", "13C", "4C", "2C"], 6), # Look into this
		# make_test(["12S", "11S", "10S", "6S", "2S", "14H", "11H", "4H", "2H", "5C", "14D", "11D", "3D"], 5), # Look into this
		# make_test(["12S", "11S", "6S", "5S", "4S", "8H", "5H", "14C", "13C", "9C", "3D", "13D", "7D"], 5), # Look into this
		# make_test(["14S", "10S", "9S", "4S", "9H", "7H", "3H", "7C", "2C", "13D", "12D", "9D", "2D"], 3), # Look into this

		# make_test(["13S", "10S", "9S", "8S", "6S", "8H", "12C", "10C", "3C", "2C", "7D", "6D", "2D"], 3), # Look into this
		# make_test(["11S", "10S", "9S", "8S", "6S", "13H", "7H", "6H", "3H", "13C", "5C", "13D", "12D"], 6), # Look into this
		# make_test(["13S", "10S", "6S", "5S", "3S", "14C", "10C", "7C", "3C", "14D", "13D", "12D", "7D"], 6), # Look into this
		# make_test(["14S", "10S", "8S", "7S", "6S", "12H", "6H", "13C", "10C", "7C", "4C", "13D", "7D"], 5), # Look into this
		# make_test(["14S", "13S", "12S", "7S", "4S", "11H", "7H", "10C", "5C", "14D", "12D", "6D", "3D"], 5), # Look into this
		
		# make_test(["13S", "6S", "5S", "4S", "2S", "10H", "8H", "5H", "2H", "14C", "13C", "7C", "14D"], 5), # self dealer gives 4 other's bid [1, 3, 4]
		# make_test(["12S", "10S", "9S", "8S", "12H", "11H", "9H", "8H", "11C", "5C", "4C", "7D", "3D"], 2), # self dealer gives 4 other's bid [1, 3, 4]


		# # ----------------
		# make_test(["10S", "7S", "2S", "14H", "13H", "10H", "3H", "12C", "11C", "7C", "14D", "8D", "3D"], 3), # self dealer gives 4 other's bid [1, 3, 4]
		# make_test(["14S", "13S", "12S", "3S", "14H", "11H", "8H", "6H", "10C", "6C", "13D", "7D", "5D"], 5), # self dealer gives 4 other's bid [1, 3, 4]

		# make_test(["14S", "12S", "11S", "10S", "9S", "7S", "5S", "3H", "13C", "12C", "5C", "8D", "2D"], 8), # self dealer gives 4 other's bid [1, 3, 4]
		# make_test(["14S", "12S", "11S", "8S", "7S", "4S", "14C", "7C", "6C", "2C", "12D", "4D", "3D"], 6), # self dealer gives 4 other's bid [1, 3, 4]
		# make_test(["13S", "12S", "10S", "4S", "2S", "14H", "13H", "2H", "6C", "2C", "13D", "12D", "2D"], 6), # self dealer gives 4 other's bid [1, 3, 4]
		
		# make_test(["14S", "12S", "8S", "4S", "3S", "2S", "13H", "7H", "6H", "3C", "12D", "9D", "3D"], 5), 
		# make_test(["13S", "10S", "9S", "6S", "5S", "9H", "4H", "14C", "12C", "4C", "13D", "11D", "6D"], 5), 
		# make_test(["14S", "10S", "8S", "6S", "4S", "3S", "2S", "13H", "12H", "9H", "9C", "7C", "8D"], 6), 

		# print()

		# make_test(["12S", "7S", "8S", "6S", "4S", "3S", "2S", "13H", "12H", "9H", "9C", "7C", "8D"], 6), 
		# ""

		
	]
	
	print("\n==================== BID TESTS ====================\n")

	tests = cv_tests


	var passed = 0
	var failed = 0

	var failed_indx: Array = []
	var failed_arr: Array = []
	
	for i in range(len(tests)):
		var test = tests[i]

		# print(test)
		var calculated_bid = run_test(test)
		var expected = test["expected"]
		var cards_str = PoolStringArray(test["cards"]).join(", ")
		
		if expected == -1:
			# No expected value, just print result
			# print("[%d] Cards: %s" % [i + 1, cards_str])
			# print("    Bid: %d\n" % calculated_bid)
			pass
		elif calculated_bid == expected:
			passed += 1
			# print("[%d] PASS - Expected: %d, Got: %d" % [i + 1, expected, calculated_bid])
			# print("    Cards: %s\n" % cards_str)
		else:
			failed += 1
			print("[%d] FAIL - Expected: %d, Got: %d" % [i + 1, expected, calculated_bid])
			print("    Cards: %s\n" % cards_str)

			# failed_arr.append(i)
			
			failed_arr.append_array([test['cards']])
			failed_indx.append_array([[expected, calculated_bid]])
	
	
	print("Passed: %d, Failed: %d, Total: %d" % [passed, failed, passed + failed])
	# print(failed_indx)
	print("=================================================\n")

	for indx in len(failed_arr):
		print(failed_arr[indx], " \t", "Expected Bid: ", failed_indx[indx][0], "\t", "Calculated Bid: ", failed_indx[indx][1] )
		# pass


func run_test(p_test: Dictionary) -> int:
	var m_game_helper = mock_game_helper.new()

	var m_players: Array = create_all_players(
		p_test["player_details"],
		p_test["cards"],
		p_test["player_to_bid_ingame_id"]
	)

	var m_dealer = get_player_by_id(m_players, p_test["dealer_in_game_id"])
	var m_player_to_bid = get_player_by_id(m_players, p_test["player_to_bid_ingame_id"])
	var m_current_bid_turn: int = p_test["player_to_bid_ingame_id"] - 1
	
	var m_match_info = match_info_bot.new(
		p_test["game_mode"],
		m_current_bid_turn,
		p_test["current_game_round"],
		m_dealer
	)

	var m_bot_logic = bot_logic.new(
		m_player_to_bid,
		m_game_helper,
		m_players
	)

	m_bot_logic.manual_card_distribution_completed()

	return m_bot_logic.select_bid_amount(
		m_match_info,
		p_test.get("grand_total_score", {})
	)


func create_all_players(
	p_player_details: Array,
	p_bid_player_cards: Array,
	p_bid_player_in_game_id: int
) -> Array:
	var m_players: Array = []

	for m_player_info in p_player_details:
		var m_in_game_id: int = m_player_info["in_game_id"]
		var m_cards: Array = [] if m_in_game_id != p_bid_player_in_game_id else p_bid_player_cards
		
		var m_player = mock_player.new(
			m_cards,
			m_player_info["username"],
			m_in_game_id
		)
		m_players.append(m_player)

	return m_players


func get_player_by_id(p_all_players: Array, p_id: int):
	for m_player in p_all_players:
		if m_player.in_game_id == p_id:
			return m_player
	return null
