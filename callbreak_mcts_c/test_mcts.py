import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from mcts_bridge import mcts_search
import sys
import os
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from bid_logic import Suit

def test_mcts():
    print("--- STARTING PYTHON TEST SCRIPT ---")
    
    suits = ["S", "D", "C", "H"]
    full_deck = [f"{rank}{suit}" for suit in suits for rank in range(2, 15)]
    
    args = {
        "original_deck": full_deck,
        "known_hand": ["4C", "9S", "8S", "10D", "5H", "14D", "11C", "12D", "7H", "2S"],
        "bids": [2, 3, 1, 4],
        "tricks_won": [0, 1, 0, 2],
        "current_turn": 0,
        "cards_played": ["5S", "6H"],
        "trick_starter": 2,
        "discard_pile": ["7D", "8C", "9D", "10C", "2D", "3D", "4D", "5D", "6D", "7C", "8H", "9H"],
        "discard_starters": [1, 2, 3],
        "led_suit": Suit.SPADES,
        "void_tracker": [{Suit.DIAMONDS}, {Suit.CLUBS, Suit.HEARTS}, {Suit.SPADES}, set()],
        "player_index": 0,
        "player_in_game_ids": [101, 205, 309, 411],
        "iterations": 2000,
        "simulations_per_det": 10,
        "time_limit_ms": 0,
        "block_leader": True,
        "cumulative_scores": [1.5, -2.0, 3.1, 4.0],
        "current_round": 4,
        "total_rounds": 5,
    }
    
    print("Calling mcts_search...")
    best_card, actions = mcts_search(**args)
    print(f" best_card: {best_card}")
    for k, v in actions.items():
        print(f" {k}: {v}")
    
    print("--- END PYTHON TEST SCRIPT ---")

if __name__ == "__main__":
    test_mcts()
