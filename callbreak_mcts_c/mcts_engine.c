/*
 * mcts_engine.c — Complete Callbreak MCTS engine in C.
 *
 * Exact port of the Python logic from:
 *   - mcts_state.py    (CallbreakState, play_card_inplace, get_legal_moves, etc.)
 *   - utils.py         (distribute_hidden_cards, get_legal_cards, get_winning_card)
 *   - callbreak_mcts_node.py (mcts_search, rollout, heuristic, UCB1)
 *
 * Card encoding: uint8_t = (rank << 2) | suit
 *   rank 2-14, suit 0=D, 1=C, 2=H, 3=S
 */

#include "mcts_engine.h"
#include "bot_logic_c.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>
#include <time.h>

/* ======================================================================
 * Configuration
 * ====================================================================== */
#define EXPLORATION_C  1.414
#define HEURISTIC_PROB 0.95   /* 95% heuristic, 20% random in rollout */
#define INFER_MAX_RETRIES 300  /* inference-guided distribution retries */
#define MAX_RETRIES    2000    /* distribute_hidden_cards retries */

/* Inference-guided determinization tuning knobs */
#define INFER_SUIT_FOLLOW_BOOST   1.18  /* repeated follows imply longer suit length */
#define INFER_TRUMP_SPADE_BOOST   1.20  /* prior trumping implies extra spade density */
#define INFER_SHORT_SUIT_PENALTY  0.78  /* reduce honors in suits the player has bled low */
#define INFER_HONOR_MISS_PENALTY  0.92  /* mild honor penalty after repeated same-suit plays */
#define INFER_VOID_CONCENTRATION_WEIGHT 0.30 /* concentrate cards into the suits still available */
#define INFER_CURRENT_FOLLOW_BOOST 1.22 /* current-trick led suit is more likely for pending players */
#define INFER_CURRENT_TRUMP_BOOST  1.14 /* pending players who may need to cut lean slightly spade-heavy */
#define INFER_CURRENT_OFFSUIT_PENALTY 0.88 /* lower off-suit density when follow is still possible */
#define INFER_HIGH_BID_TARGET     5
#define INFER_LOW_BID_TARGET      2
#define INFER_BID_STRENGTH_BONUS  0.30
#define INFER_BID_STRENGTH_PENALTY 0.22
#define INFER_STRONG_CARD_THRESHOLD 0.60
#define INFER_STRONG_TARGET_SHIFT  0.16
#define INFER_STRONG_TARGET_BONUS  0.26
#define INFER_STRONG_TARGET_PENALTY 0.14
#define INFER_SLOT_PRESSURE_WEIGHT 0.90
#define INFER_SCORE_MIN           0.001 /* floor to prevent zero-probability */
#define INFER_SCORE_MAX           10.0  /* ceiling to cap runaway inference */
#define INFER_NOISE_MIN           0.96
#define INFER_NOISE_MAX           1.04
#define LOW_RANK_THRESHOLD        7     /* rank <= this counts as "low" for discard heuristic */
#define HIGH_RANK_THRESHOLD       11    /* rank >= this counts as "high/honor" */
#define INITIAL_HIDDEN_UNSEEN     39
#define INITIAL_OPPONENT_HAND     13
#define ADAPTIVE_MIN_EFFORT_RATIO 0.20
#define ADAPTIVE_SPACE_WEIGHT     0.60
#define ADAPTIVE_UNSEEN_WEIGHT    0.25
#define ADAPTIVE_HAND_WEIGHT      0.15
#define PIMC_CONFIDENCE_MARGIN_MAX 0.030
#define PIMC_CONFIDENCE_MARGIN_MIN 0.012

/* ======================================================================
 * Random number helpers
 * ====================================================================== */
static unsigned int _rng_state = 0;
static int _rng_initialized = 0;

static double _adaptive_confidence_margin(const SearchParams *params, int unseen_count) {
    int normalized_total_rounds = 5;
    int normalized_current_round = 1;
    if (params) {
        if (params->total_rounds > 0) normalized_total_rounds = params->total_rounds;
        normalized_current_round = params->current_round;
    }

    if (normalized_current_round < 1) normalized_current_round = 1;
    if (normalized_current_round > normalized_total_rounds) {
        normalized_current_round = normalized_total_rounds;
    }

    double urgency = (normalized_total_rounds > 1)
        ? (double)(normalized_current_round - 1) / (double)(normalized_total_rounds - 1)
        : 1.0;
    double late_pressure = 0.5 * urgency + 0.5 * urgency * urgency;
    double hidden_ratio = fmax(0.0, fmin(1.0,
        (double)unseen_count / (double)INITIAL_HIDDEN_UNSEEN));

    /* Stay highly conservative in the early match, then gradually relax the
     * heuristic lock once placement urgency is high and the hidden world has narrowed. */
    double horizon_relax = late_pressure * (0.75 + 0.25 * (1.0 - hidden_ratio));
    double margin = PIMC_CONFIDENCE_MARGIN_MAX
        - (PIMC_CONFIDENCE_MARGIN_MAX - PIMC_CONFIDENCE_MARGIN_MIN) * horizon_relax;

    if (margin < PIMC_CONFIDENCE_MARGIN_MIN) return PIMC_CONFIDENCE_MARGIN_MIN;
    if (margin > PIMC_CONFIDENCE_MARGIN_MAX) return PIMC_CONFIDENCE_MARGIN_MAX;
    return margin;
}

static void _ensure_rng(void) {
    if (!_rng_initialized) {
        _rng_state = (unsigned int)time(NULL) ^ (unsigned int)clock();
        _rng_initialized = 1;
    }
}

/* xorshift32 for fast pseudo-random numbers */
static unsigned int _rand_next(void) {
    _rng_state ^= _rng_state << 13;
    _rng_state ^= _rng_state >> 17;
    _rng_state ^= _rng_state << 5;
    return _rng_state;
}

int _rand_int(int n) {
    if (n <= 1) return 0;
    return (int)(_rand_next() % (unsigned int)n);
}

double _rand_double(void) {
    return (double)(_rand_next() & 0x7FFFFFFF) / (double)0x7FFFFFFF;
}

/* Fisher-Yates shuffle */
static void _shuffle(uint8_t *arr, int n) {
    for (int i = n - 1; i > 0; i--) {
        int j = _rand_int(i + 1);
        uint8_t tmp = arr[i];
        arr[i] = arr[j];
        arr[j] = tmp;
    }
}

/* ======================================================================
 * Card utility functions
 * ====================================================================== */

/* Check if card is in array */
static int _card_in(uint8_t card, const uint8_t *arr, int n) {
    for (int i = 0; i < n; i++) {
        if (arr[i] == card) return 1;
    }
    return 0;
}

/* Remove card from array, returns new size */
static int _card_remove(uint8_t *arr, int n, uint8_t card) {
    for (int i = 0; i < n; i++) {
        if (arr[i] == card) {
            arr[i] = arr[n - 1];
            return n - 1;
        }
    }
    return n; /* not found, shouldn't happen */
}

/* Sort cards: by suit order (S=0,H=1,C=2,D=3) then by rank descending */
static int _suit_sort_order(uint8_t c) {
    int s = CARD_SUIT(c);
    /* S=3->0, H=2->1, C=1->2, D=0->3 */
    switch (s) {
        case SUIT_SPADES:   return 0;
        case SUIT_HEARTS:   return 1;
        case SUIT_CLUBS:    return 2;
        case SUIT_DIAMONDS: return 3;
        default: return 4;
    }
}

static int _card_cmp(const void *a, const void *b) {
    uint8_t ca = *(const uint8_t *)a;
    uint8_t cb = *(const uint8_t *)b;
    int sa = _suit_sort_order(ca);
    int sb = _suit_sort_order(cb);
    if (sa != sb) return sa - sb;
    /* Descending rank */
    return CARD_RANK(cb) - CARD_RANK(ca);
}

static void _sort_hand(uint8_t *hand, int n) {
    qsort(hand, (size_t)n, sizeof(uint8_t), _card_cmp);
}

static double _clamp_double(double v, double lo, double hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

static int _count_played_cards_of_suit(
    const uint8_t *cards, int count, int suit
) {
    int total = 0;
    for (int i = 0; i < count; i++) {
        if (CARD_SUIT(cards[i]) == suit) total++;
    }
    return total;
}

static int _player_pending_in_current_trick(const InferenceCtx *ctx, int player) {
    if (ctx->current_trick_count <= 0) return 0;
    int offset = (player - ctx->current_trick_starter + NUM_PLAYERS) % NUM_PLAYERS;
    return offset >= ctx->current_trick_count && offset < NUM_PLAYERS;
}

static double _player_strength_desire(
    int player,
    const InferenceCtx *ctx,
    const int *bids,
    const int *tricks_won
) {
    int cards_left = ctx->remaining_card_counts[player] > 0
        ? ctx->remaining_card_counts[player]
        : 1;
    int tricks_needed = bids[player] - tricks_won[player];
    double bid_signal = _clamp_double(((double)bids[player] - 3.0) / 4.0, -0.75, 1.25);
    double urgency = _clamp_double((double)tricks_needed / (double)cards_left, -1.0, 1.0);
    return 0.55 * bid_signal + 0.45 * urgency;
}

static double _card_strength_value(uint8_t card) {
    int suit = CARD_SUIT(card);
    int rank = CARD_RANK(card);
    double strength = 0.0;

    if (suit == SUIT_SPADES) {
        strength += 0.35 + 0.10 * (double)(rank - 2);
    }

    if (rank >= HIGH_RANK_THRESHOLD) {
        strength += 0.20 + 0.16 * (double)(rank - HIGH_RANK_THRESHOLD);
    } else if (rank >= 9) {
        strength += 0.05 * (double)(rank - 8);
    }

    return strength;
}

static int _count_remaining_compatible_cards(
    const double *scores,
    int unseen_count,
    const uint8_t *assigned,
    int player,
    int skip_card_idx
) {
    int total = 0;
    for (int ci = 0; ci < unseen_count; ci++) {
        if (ci == skip_card_idx || assigned[ci]) continue;
        if (scores[player * TOTAL_CARDS + ci] > 0.0) total++;
    }
    return total;
}

static void _observe_trick_play(
    InferenceCtx *ctx,
    int player,
    int led_suit,
    uint8_t card,
    int trick_trumped_before,
    int highest_led_before,
    int highest_spade_before
) {
    int cs = CARD_SUIT(card);
    int cr = CARD_RANK(card);

    if (cs != led_suit) {
        ctx->void_suits[player] |= VOID_BIT(led_suit);
    }

    if (cs == led_suit) {
        ctx->suit_follow_count[player][cs]++;

        if (!trick_trumped_before) {
            if (cr < highest_led_before) {
                if (highest_led_before < ctx->max_rank[player][cs]) {
                    ctx->max_rank[player][cs] = highest_led_before;
                }
            }
        }
        return;
    }

    if (cs == SUIT_SPADES) {
        if (led_suit != SUIT_SPADES) {
            ctx->trump_count[player]++;
        }

        if (trick_trumped_before && cr < highest_spade_before
            && highest_spade_before < ctx->max_rank[player][SUIT_SPADES]) {
            ctx->max_rank[player][SUIT_SPADES] = highest_spade_before;
        }
        return;
    }

    if (cr <= LOW_RANK_THRESHOLD) {
        ctx->low_discard_suits[player] |= VOID_BIT(cs);
    }

    if (!trick_trumped_before && led_suit != SUIT_SPADES) {
        ctx->void_suits[player] |= VOID_BIT(SUIT_SPADES);
    } else if (trick_trumped_before) {
        if (highest_spade_before < ctx->max_rank[player][SUIT_SPADES]) {
            ctx->max_rank[player][SUIT_SPADES] = highest_spade_before;
        }
    }
}

static void _advance_trick_state(
    int led_suit,
    uint8_t card,
    int *trick_trumped,
    int *highest_led,
    int *highest_spade
) {
    int cs = CARD_SUIT(card);
    int cr = CARD_RANK(card);

    if (cs == led_suit) {
        if (cr > *highest_led) *highest_led = cr;
        if (led_suit == SUIT_SPADES && cr > *highest_spade) {
            *highest_spade = cr;
        }
        return;
    }

    if (cs == SUIT_SPADES) {
        *trick_trumped = 1;
        if (cr > *highest_spade) *highest_spade = cr;
    }
}

static int _assignment_stays_feasible(
    const double *scores,
    int unseen_count,
    const int *remaining_slots,
    const uint8_t *assigned,
    int assign_card_idx,
    int assign_player
) {
    for (int p = 0; p < NUM_PLAYERS; p++) {
        int need = remaining_slots[p] - (p == assign_player ? 1 : 0);
        if (need < 0) return 0;
        if (need == 0) continue;

        int compatible = _count_remaining_compatible_cards(
            scores, unseen_count, assigned, p, assign_card_idx
        );
        if (compatible < need) return 0;
    }
    return 1;
}

static int _legal_random_distribute_from_scores(
    const double *scores,
    const uint8_t *unseen,
    int unseen_count,
    CallbreakState *state,
    const int *needed,
    int root_idx
) {
    int card_order[TOTAL_CARDS];
    int eligible_counts[TOTAL_CARDS];
    for (int ci = 0; ci < unseen_count; ci++) {
        int eligible = 0;
        for (int p = 0; p < NUM_PLAYERS; p++) {
            if (p == root_idx || needed[p] == 0) continue;
            if (scores[p * TOTAL_CARDS + ci] > 0.0) eligible++;
        }
        card_order[ci] = ci;
        eligible_counts[ci] = eligible;
    }

    for (int i = 0; i < unseen_count - 1; i++) {
        for (int j = i + 1; j < unseen_count; j++) {
            int ci = card_order[i];
            int cj = card_order[j];
            if (eligible_counts[cj] < eligible_counts[ci]) {
                int tmp = card_order[i];
                card_order[i] = card_order[j];
                card_order[j] = tmp;
            }
        }
    }

    for (int attempt = 0; attempt < MAX_RETRIES; attempt++) {
        uint8_t temp_hands[NUM_PLAYERS][CARDS_PER_HAND];
        int temp_sizes[NUM_PLAYERS];
        memset(temp_sizes, 0, sizeof(temp_sizes));
        int remaining_slots[NUM_PLAYERS];
        memcpy(remaining_slots, needed, sizeof(remaining_slots));
        uint8_t assigned[TOTAL_CARDS];
        memset(assigned, 0, sizeof(assigned));
        int success = 1;

        for (int order_idx = 0; order_idx < unseen_count; order_idx++) {
            int ci = card_order[order_idx];
            if (assigned[ci]) continue;

            int total_remaining = 0;
            for (int p = 0; p < NUM_PLAYERS; p++) total_remaining += remaining_slots[p];
            if (total_remaining <= 0) break;

            int candidates[NUM_PLAYERS];
            int candidate_count = 0;
            for (int p = 0; p < NUM_PLAYERS; p++) {
                if (p == root_idx || remaining_slots[p] <= 0) continue;
                if (scores[p * TOTAL_CARDS + ci] <= 0.0) continue;
                if (!_assignment_stays_feasible(
                        scores, unseen_count, remaining_slots,
                        assigned, ci, p)) {
                    continue;
                }
                candidates[candidate_count++] = p;
            }

            if (candidate_count == 0) {
                success = 0;
                break;
            }

            int chosen_player = candidates[_rand_int(candidate_count)];
            temp_hands[chosen_player][temp_sizes[chosen_player]++] = unseen[ci];
            remaining_slots[chosen_player]--;
            assigned[ci] = 1;
        }

        for (int p = 0; p < NUM_PLAYERS; p++) {
            if (remaining_slots[p] != 0) {
                success = 0;
                break;
            }
        }

        if (success) {
            for (int i = 0; i < NUM_PLAYERS; i++) {
                if (i == root_idx || needed[i] == 0) continue;
                memcpy(state->hands[i], temp_hands[i], (size_t)temp_sizes[i]);
                state->hand_sizes[i] = temp_sizes[i];
            }
            return 1;
        }

        for (int i = unseen_count - 1; i > 0; i--) {
            int j = _rand_int(i + 1);
            int tmp = card_order[i];
            card_order[i] = card_order[j];
            card_order[j] = tmp;
        }
    }

    return 0;
}

static double _card_player_compatibility_score(
    uint8_t card,
    int player,
    const CallbreakState *state,
    const InferenceCtx *ctx,
    const int *bids,
    const int *tricks_won
) {
    int suit = CARD_SUIT(card);
    int rank = CARD_RANK(card);
    uint8_t voids = state->void_suits_by_player[player] | ctx->void_suits[player];

    if (voids & VOID_BIT(suit)) return 0.0;
    if (rank > ctx->max_rank[player][suit]) return 0.0;

    double score = 1.0;
    int void_count = __builtin_popcount((unsigned int)voids);
    int follow_count = ctx->suit_follow_count[player][suit];
    int same_suit_seen = _count_played_cards_of_suit(
        ctx->played_cards_by_player[player],
        ctx->played_card_counts[player],
        suit
    );
    double strength = _card_strength_value(card);
    double desire = _player_strength_desire(player, ctx, bids, tricks_won);

    if (void_count > 0) {
        score *= 1.0 + INFER_VOID_CONCENTRATION_WEIGHT * ((double)void_count / 3.0);
    }

    if (follow_count > 0) {
        score *= pow(INFER_SUIT_FOLLOW_BOOST, (double)follow_count);
    }

    if (same_suit_seen > 0) {
        score *= 1.0 + 0.06 * (double)same_suit_seen;
    }

    if (suit == SUIT_SPADES && ctx->trump_count[player] > 0) {
        score *= 1.0 + 0.12 * (double)ctx->trump_count[player];
        score *= INFER_TRUMP_SPADE_BOOST;
    }

    if ((ctx->low_discard_suits[player] & VOID_BIT(suit))
        && rank >= HIGH_RANK_THRESHOLD) {
        score *= INFER_SHORT_SUIT_PENALTY;
    }

    if (rank >= 12 && same_suit_seen > 1) {
        score *= pow(INFER_HONOR_MISS_PENALTY, (double)(same_suit_seen - 1));
    }

    if (_player_pending_in_current_trick(ctx, player) && ctx->current_trick_count > 0) {
        int led = CARD_SUIT(ctx->current_trick[0]);
        if (!(voids & VOID_BIT(led))) {
            if (suit == led) {
                score *= INFER_CURRENT_FOLLOW_BOOST;
            } else if (led != SUIT_SPADES && suit == SUIT_SPADES) {
                score *= INFER_CURRENT_TRUMP_BOOST;
            } else {
                score *= INFER_CURRENT_OFFSUIT_PENALTY;
            }
        } else if (led != SUIT_SPADES && !(voids & VOID_BIT(SUIT_SPADES))) {
            if (suit == SUIT_SPADES) {
                score *= INFER_CURRENT_TRUMP_BOOST;
            }
        }
    }

    if (strength > 0.0) {
        if (desire >= 0.0) {
            score *= 1.0 + desire * strength * INFER_BID_STRENGTH_BONUS;
        } else {
            score *= fmax(0.25, 1.0 + desire * strength * INFER_BID_STRENGTH_PENALTY);
        }
    }

    if (bids[player] >= INFER_HIGH_BID_TARGET
        && suit == SUIT_SPADES && rank >= 10) {
        score *= 1.08;
    }

    if (bids[player] <= INFER_LOW_BID_TARGET) {
        if (suit == SUIT_SPADES && rank >= HIGH_RANK_THRESHOLD) score *= 0.86;
        if (suit != SUIT_SPADES && rank == 14) score *= 0.90;
    }

    if (bids[player] > 0 && tricks_won[player] >= bids[player] && strength > 0.0) {
        score *= fmax(0.70, 1.0 - 0.08 * strength);
    }

    score *= INFER_NOISE_MIN + _rand_double() * (INFER_NOISE_MAX - INFER_NOISE_MIN);
    if (score < INFER_SCORE_MIN) score = INFER_SCORE_MIN;
    if (score > INFER_SCORE_MAX) score = INFER_SCORE_MAX;
    return score;
}

/* ======================================================================
 * get_winning_index — port of _get_winning_index from mcts_state.py
 * ====================================================================== */
static int _get_winning_index(const uint8_t *cards, int count) {
    int led_suit = CARD_SUIT(cards[0]);
    int best_rank = CARD_RANK(cards[0]);
    int best_suit = led_suit;
    int best_idx = 0;

    for (int i = 1; i < count; i++) {
        int cs = CARD_SUIT(cards[i]);
        int cr = CARD_RANK(cards[i]);

        if (cs == SUIT_SPADES && best_suit != SUIT_SPADES) {
            best_rank = cr;
            best_suit = cs;
            best_idx = i;
        } else if (cs == best_suit && cr > best_rank) {
            best_rank = cr;
            best_idx = i;
        }
    }
    return best_idx;
}

/* ======================================================================
 * get_legal_cards — matches Godot's LegalCardsDetector rules exactly.
 *
 * Rules:
 *  1. Leading (played_count==0): all cards legal.
 *  2. Following suit: must play led-suit cards HIGHER than the highest
 *     led-suit card already played (if you have any); otherwise any
 *     led-suit card.
 *  3. Can't follow suit, trick NOT trumped: must play spades (any).
 *  4. Can't follow suit, trick IS trumped: must play a HIGHER spade
 *     than the highest spade already played; if no higher spade, play
 *     ANYTHING (including non-spade off-suits).
 *  5. Led suit IS spades: treat as "following suit" with must-play-higher.
 *  6. No led-suit and no spades: play anything.
 * ====================================================================== */

/* Helper: check if any card in played[] is a spade (excluding the first
 * card, which defines the led suit). */
static int _is_trumped(const uint8_t *played, int played_count) {
    if (played_count < 2) return 0;
    int led = CARD_SUIT(played[0]);
    if (led == SUIT_SPADES) return 0; /* spade-led is not "trumped" */
    for (int i = 1; i < played_count; i++) {
        if (CARD_SUIT(played[i]) == SUIT_SPADES) return 1;
    }
    return 0;
}

/* Helper: highest rank of a given suit in a card array */
static int _highest_rank_of_suit(const uint8_t *cards, int n, int suit) {
    int best = -1;
    for (int i = 0; i < n; i++) {
        if (CARD_SUIT(cards[i]) == suit && CARD_RANK(cards[i]) > best)
            best = CARD_RANK(cards[i]);
    }
    return best;
}

int get_legal_cards_c(
    const uint8_t *hand, int hand_size,
    const uint8_t *played, int played_count,
    int led_suit,
    uint8_t *out_legal
) {
    /* Leading or no led suit: all cards are legal */
    if (played_count == 0 || led_suit < 0) {
        memcpy(out_legal, hand, (size_t)hand_size);
        return hand_size;
    }

    /* Collect cards by category */
    uint8_t led_cards[CARDS_PER_HAND];     int led_count = 0;
    uint8_t spade_cards[CARDS_PER_HAND];   int spade_count = 0;

    for (int i = 0; i < hand_size; i++) {
        int s = CARD_SUIT(hand[i]);
        if (s == led_suit)      led_cards[led_count++] = hand[i];
        if (s == SUIT_SPADES)   spade_cards[spade_count++] = hand[i];
    }

    /* Case: led suit is spades */
    if (led_suit == SUIT_SPADES) {
        if (spade_count == 0) {
            /* No spades at all: play anything */
            memcpy(out_legal, hand, (size_t)hand_size);
            return hand_size;
        }
        /* Must play higher spade if possible */
        int best_played_rank = _highest_rank_of_suit(played, played_count, SUIT_SPADES);
        int count = 0;
        for (int i = 0; i < spade_count; i++) {
            if (CARD_RANK(spade_cards[i]) > best_played_rank)
                out_legal[count++] = spade_cards[i];
        }
        if (count > 0) return count;
        /* No higher spades: play any spade */
        memcpy(out_legal, spade_cards, (size_t)spade_count);
        return spade_count;
    }

    /* Case: have led-suit cards */
    if (led_count > 0) {
        if (played_count == 1) {
            /* Second turn: must play higher than first card if possible */
            int first_rank = CARD_RANK(played[0]);
            int count = 0;
            for (int i = 0; i < led_count; i++) {
                if (CARD_RANK(led_cards[i]) > first_rank)
                    out_legal[count++] = led_cards[i];
            }
            if (count > 0) return count;
            /* No higher: play any led-suit card */
            memcpy(out_legal, led_cards, (size_t)led_count);
            return led_count;
        } else {
            /* Third/fourth turn: must play higher than highest led-suit if not trumped,
             * or just follow suit if trumped */
            int trumped = _is_trumped(played, played_count);
            if (trumped) {
                /* Trick is trumped: just follow suit (any led-suit card) */
                memcpy(out_legal, led_cards, (size_t)led_count);
                return led_count;
            }
            /* Not trumped: must play higher same-suit */
            int best_led_rank = _highest_rank_of_suit(played, played_count, led_suit);
            int count = 0;
            for (int i = 0; i < led_count; i++) {
                if (CARD_RANK(led_cards[i]) > best_led_rank)
                    out_legal[count++] = led_cards[i];
            }
            if (count > 0) return count;
            /* No higher: play any led-suit card */
            memcpy(out_legal, led_cards, (size_t)led_count);
            return led_count;
        }
    }

    /* Case: no led-suit cards */
    if (spade_count == 0) {
        /* No spades either: play anything */
        memcpy(out_legal, hand, (size_t)hand_size);
        return hand_size;
    }

    /* Have spades but not led suit */
    int trumped = _is_trumped(played, played_count);
    if (!trumped) {
        /* Not trumped: must play a spade (any) */
        memcpy(out_legal, spade_cards, (size_t)spade_count);
        return spade_count;
    }

    /* Trumped: must play a HIGHER spade if possible */
    int best_spade_rank = _highest_rank_of_suit(played, played_count, SUIT_SPADES);
    int count = 0;
    for (int i = 0; i < spade_count; i++) {
        if (CARD_RANK(spade_cards[i]) > best_spade_rank)
            out_legal[count++] = spade_cards[i];
    }
    if (count > 0) return count;

    /* No higher spade: can play ANYTHING */
    memcpy(out_legal, hand, (size_t)hand_size);
    return hand_size;
}

/* ======================================================================
 * CallbreakState operations — port from mcts_state.py
 * ====================================================================== */

static void _state_copy(CallbreakState *dst, const CallbreakState *src) {
    memcpy(dst, src, sizeof(CallbreakState));
}

static void _state_play_card_inplace(CallbreakState *s, uint8_t card) {
    int cur = s->current_turn;

    /* Remove card from hand */
    s->hand_sizes[cur] = _card_remove(
        s->hands[cur], s->hand_sizes[cur], card
    );

    /* Update Authoritative History */
    s->played_cards_by_player[cur][s->played_card_counts[cur]++] = card;
    s->remaining_card_counts[cur]--;

    /* Set led suit on first card */
    if (s->cards_played_count == 0) {
        s->led_suit = CARD_SUIT(card);
    } else {
        /* If player didn't follow the led suit, they are void in it */
        if (CARD_SUIT(card) != s->led_suit) {
            s->void_suits_by_player[cur] |= VOID_BIT(s->led_suit);
        }
    }

    /* Add to current trick */
    s->cards_played[s->cards_played_count++] = card;
    s->total_cards_played++;

    /* Advance turn */
    s->current_turn = (cur + 1) % NUM_PLAYERS;

    /* Trick complete? */
    if (s->cards_played_count == NUM_PLAYERS) {
        int win_off = _get_winning_index(s->cards_played, NUM_PLAYERS);
        int winner = (s->trick_starter + win_off) % NUM_PLAYERS;
        
        /* Save trick history */
        if (s->completed_tricks_count < MAX_TRICKS) {
            TrickRecord *tr = &s->trick_history[s->completed_tricks_count++];
            memcpy(tr->cards, s->cards_played, NUM_PLAYERS);
            tr->starter = s->trick_starter;
            tr->led_suit = s->led_suit;
            tr->winner = winner;
            for (int tt = 0; tt < NUM_PLAYERS; tt++) {
                int p = (s->trick_starter + tt) % NUM_PLAYERS;
                tr->player_ids[tt] = s->in_game_ids[p];
            }
        }

        s->tricks_won[winner]++;
        s->cards_played_count = 0;
        s->led_suit = -1;
        s->current_turn = winner;
        s->trick_starter = winner;
    }
}

void state_play_card_inplace_c(CallbreakState *state, uint8_t card) {
    _state_play_card_inplace(state, card);
}

static double _compute_effort_scale(
    const int *opponent_card_counts,
    int unseen_count
) {
    int total_hidden = 0;
    int max_hand = 0;
    double hidden_log_space = 0.0;
    const double initial_log_space =
        lgamma((double)INITIAL_HIDDEN_UNSEEN + 1.0)
        - 3.0 * lgamma((double)INITIAL_OPPONENT_HAND + 1.0);

    for (int i = 0; i < NUM_PLAYERS; i++) {
        int count = opponent_card_counts[i];
        if (count <= 0) continue;
        total_hidden += count;
        if (count > max_hand) max_hand = count;
        hidden_log_space -= lgamma((double)count + 1.0);
    }

    if (total_hidden <= 0 || unseen_count <= 0 || initial_log_space <= 0.0) {
        return ADAPTIVE_MIN_EFFORT_RATIO;
    }

    hidden_log_space += lgamma((double)total_hidden + 1.0);

    double hidden_space_ratio = hidden_log_space / initial_log_space;
    if (hidden_space_ratio < 0.0) hidden_space_ratio = 0.0;
    if (hidden_space_ratio > 1.0) hidden_space_ratio = 1.0;

    double unseen_ratio = (double)unseen_count / (double)INITIAL_HIDDEN_UNSEEN;
    if (unseen_ratio < 0.0) unseen_ratio = 0.0;
    if (unseen_ratio > 1.0) unseen_ratio = 1.0;

    double max_hand_ratio = (double)max_hand / (double)INITIAL_OPPONENT_HAND;
    if (max_hand_ratio < 0.0) max_hand_ratio = 0.0;
    if (max_hand_ratio > 1.0) max_hand_ratio = 1.0;

    double combined =
        ADAPTIVE_SPACE_WEIGHT * hidden_space_ratio
        + ADAPTIVE_UNSEEN_WEIGHT * unseen_ratio
        + ADAPTIVE_HAND_WEIGHT * max_hand_ratio;

    if (combined < 0.0) combined = 0.0;
    if (combined > 1.0) combined = 1.0;

    return ADAPTIVE_MIN_EFFORT_RATIO
        + (1.0 - ADAPTIVE_MIN_EFFORT_RATIO) * combined;
}



/* ======================================================================
 * distribute_hidden_cards — port from utils.py
 * ====================================================================== */
static void _distribute_hidden_cards(
    uint8_t *unseen, int unseen_count,
    CallbreakState *state,
    const int *needed,     /* needed[i] = cards player i needs */
    int root_idx,
    const uint8_t *void_tracker  /* bitmask per player */
) {
    int total_needed = 0;
    for (int i = 0; i < NUM_PLAYERS; i++) total_needed += needed[i];

    if (total_needed == 0) return;

    /* Check if any constraints exist */
    int has_constraints = 0;
    for (int i = 0; i < NUM_PLAYERS; i++) {
        if (i != root_idx && void_tracker[i] != 0) {
            has_constraints = 1;
            break;
        }
    }

    if (unseen_count < total_needed) {
        /* Fallback: give whatever we can */
        _shuffle(unseen, unseen_count);
        int ci = 0;
        for (int i = 0; i < NUM_PLAYERS; i++) {
            if (i == root_idx || needed[i] == 0) continue;
            int give = needed[i];
            if (ci + give > unseen_count) give = unseen_count - ci;
            if (give <= 0) continue;
            memcpy(&state->hands[i][state->hand_sizes[i]], &unseen[ci], (size_t)give);
            state->hand_sizes[i] += give;
            ci += give;
        }
        return;
    }

    for (int attempt = 0; attempt < MAX_RETRIES; attempt++) {
        _shuffle(unseen, unseen_count);

        if (!has_constraints) {
            /* No void constraints: simple slice distribution */
            int ci = 0;
            for (int i = 0; i < NUM_PLAYERS; i++) {
                if (i == root_idx || needed[i] == 0) continue;
                memcpy(state->hands[i], &unseen[ci], (size_t)needed[i]);
                state->hand_sizes[i] = needed[i];
                ci += needed[i];
            }
            return;
        }

        /* Try to satisfy void constraints */
        uint8_t temp_hands[NUM_PLAYERS][CARDS_PER_HAND];
        int temp_sizes[NUM_PLAYERS];
        memset(temp_sizes, 0, sizeof(temp_sizes));

        uint8_t remaining[TOTAL_CARDS];
        memcpy(remaining, unseen, (size_t)unseen_count);
        int rem_count = unseen_count;
        int success = 1;

        /* Sort opponents by most constrained first (most void bits) */
        int opp_order[3];
        int opp_count = 0;
        for (int i = 0; i < NUM_PLAYERS; i++) {
            if (i != root_idx && needed[i] > 0)
                opp_order[opp_count++] = i;
        }
        /* Simple insertion sort by void bit count descending */
        for (int a = 0; a < opp_count - 1; a++) {
            for (int b = a + 1; b < opp_count; b++) {
                int va = __builtin_popcount(void_tracker[opp_order[a]]);
                int vb = __builtin_popcount(void_tracker[opp_order[b]]);
                if (vb > va) {
                    int t = opp_order[a];
                    opp_order[a] = opp_order[b];
                    opp_order[b] = t;
                }
            }
        }

        for (int oi = 0; oi < opp_count; oi++) {
            int p = opp_order[oi];
            int n = needed[p];
            uint8_t voids = void_tracker[p];

            /* Collect valid cards (suit not in void mask) */
            uint8_t valid[TOTAL_CARDS];
            int valid_count = 0;
            for (int k = 0; k < rem_count; k++) {
                if (!(voids & VOID_BIT(CARD_SUIT(remaining[k])))) {
                    valid[valid_count++] = remaining[k];
                }
            }

            if (valid_count < n) {
                success = 0;
                break;
            }

            /* Take first n valid cards */
            for (int k = 0; k < n; k++) {
                temp_hands[p][k] = valid[k];
                rem_count = _card_remove(remaining, rem_count, valid[k]);
            }
            temp_sizes[p] = n;
        }

        if (success) {
            for (int i = 0; i < NUM_PLAYERS; i++) {
                if (i == root_idx || needed[i] == 0) continue;
                memcpy(state->hands[i], temp_hands[i], (size_t)temp_sizes[i]);
                state->hand_sizes[i] = temp_sizes[i];
            }
            return;
        }
    }

    /* Fallback: ignore void constraints */
    _shuffle(unseen, unseen_count);
    int ci = 0;
    for (int i = 0; i < NUM_PLAYERS; i++) {
        if (i == root_idx || needed[i] == 0) continue;
        memcpy(state->hands[i], &unseen[ci], (size_t)needed[i]);
        state->hand_sizes[i] = needed[i];
        ci += needed[i];
    }
}

/* ======================================================================
 * Build InferenceCtx from discard pile + trick starters
 * ====================================================================== */
static void _build_inference_ctx(
    InferenceCtx *ctx,
    const uint8_t *discard_pile,
    int discard_count,
    const int *discard_starters,
    int discard_trick_count,
    const uint8_t *current_played,
    int current_played_count,
    int current_trick_starter,
    const uint8_t *void_tracker,
    const int *player_in_game_ids,
    int player_index,
    int dealer_index,
    const int *opponent_card_counts,
    int known_hand_size
) {
    memset(ctx, 0, sizeof(InferenceCtx));
    ctx->player_index = player_index;
    ctx->dealer_index = dealer_index;
    for (int i = 0; i < NUM_PLAYERS; i++) {
        ctx->player_in_game_ids[i] = player_in_game_ids ? player_in_game_ids[i] : i;
    }

    /* Copy void tracker */
    for (int i = 0; i < NUM_PLAYERS; i++)
        ctx->void_suits[i] = void_tracker[i];

    /* Remaining card counts */
    for (int i = 0; i < NUM_PLAYERS; i++) {
        if (i == player_index)
            ctx->remaining_card_counts[i] = known_hand_size;
        else
            ctx->remaining_card_counts[i] = opponent_card_counts[i];
    }

    /* Initialize max_rank bounds to 14 (Ace) */
    for (int i = 0; i < NUM_PLAYERS; i++) {
        for (int s = 0; s < NUM_SUITS; s++) {
            ctx->max_rank[i][s] = 14;
        }
    }

    /* Replay completed tricks */
    int num_tricks = discard_count / NUM_PLAYERS;
    if (discard_starters && discard_trick_count > 0 && num_tricks > discard_trick_count)
        num_tricks = discard_trick_count;
    if (num_tricks > MAX_TRICKS) num_tricks = MAX_TRICKS;
    ctx->trick_count = num_tricks;

    int hist_trick_starter = (dealer_index + 1) % NUM_PLAYERS;

    for (int t = 0; t < num_tricks; t++) {
        TrickRecord *tr = &ctx->tricks[t];
        const uint8_t *tc = &discard_pile[t * NUM_PLAYERS];
        int starter = (discard_starters && t < discard_trick_count) ? discard_starters[t] : hist_trick_starter;
        tr->starter = starter;
        tr->led_suit = CARD_SUIT(tc[0]);
        ctx->suit_led_count[tr->led_suit]++;
        memcpy(tr->cards, tc, NUM_PLAYERS);
        for (int tt = 0; tt < NUM_PLAYERS; tt++) {
            int p = (starter + tt) % NUM_PLAYERS;
            tr->player_ids[tt] = ctx->player_in_game_ids[p];
        }

        /* Find winner */
        int wi = _get_winning_index(tc, NUM_PLAYERS);
        tr->winner = (starter + wi) % NUM_PLAYERS;
        hist_trick_starter = tr->winner;

        int trick_trumped = 0;
        int highest_led = CARD_RANK(tc[0]);
        int highest_spade = (CARD_SUIT(tc[0]) == SUIT_SPADES) ? CARD_RANK(tc[0]) : -1;

        /* Record per-player data */
        for (int tt = 0; tt < NUM_PLAYERS; tt++) {
            int p = (starter + tt) % NUM_PLAYERS;
            uint8_t card = tc[tt];
            int cs = CARD_SUIT(card);
            int cr = CARD_RANK(card);

            /* Track played cards per player */
            int idx = ctx->played_card_counts[p];
            if (idx < CARDS_PER_HAND)
                ctx->played_cards_by_player[p][idx] = card;
            ctx->played_card_counts[p]++;

            /* Track specific honors played (J=11, Q=12, K=13, A=14) */
            if (cr >= 11) {
                ctx->played_honors[cs] |= (1 << cr);
            }

            if (tt > 0) {
                _observe_trick_play(
                    ctx, p, tr->led_suit, card,
                    trick_trumped, highest_led, highest_spade
                );
            }

            _advance_trick_state(
                tr->led_suit, card,
                &trick_trumped, &highest_led, &highest_spade
            );
        }
    }

    /* Copy current partial trick */
    ctx->current_trick_count = current_played_count;
    ctx->current_trick_starter = current_trick_starter;
    if (current_played_count > 0) {
        memcpy(ctx->current_trick, current_played,
               (size_t)current_played_count);
        int led = CARD_SUIT(current_played[0]);
        ctx->suit_led_count[led]++;
        
        int trick_trumped = 0;
        int highest_led = CARD_RANK(current_played[0]);
        int highest_spade = (led == SUIT_SPADES) ? highest_led : -1;

        for (int tt = 0; tt < current_played_count; tt++) {
            uint8_t card = current_played[tt];
            int cs = CARD_SUIT(card);
            int cr = CARD_RANK(card);
            if (cr >= 11) {
                ctx->played_honors[cs] |= (1 << cr);
            }
            
            /* Update authoritative per-player history with the partial trick */
            int p = (current_trick_starter + tt) % NUM_PLAYERS;
            int idx = ctx->played_card_counts[p];
            if (idx < CARDS_PER_HAND) {
                ctx->played_cards_by_player[p][idx] = card;
            }
            ctx->played_card_counts[p]++;
        }
        for (int tt = 1; tt < current_played_count; tt++) {
            int p = (current_trick_starter + tt) % NUM_PLAYERS;
            uint8_t card = current_played[tt];
            _observe_trick_play(
                ctx, p, led, card,
                trick_trumped, highest_led, highest_spade
            );
            _advance_trick_state(
                led, card,
                &trick_trumped, &highest_led, &highest_spade
            );
        }
    }
}

/* ======================================================================
 * Inference-guided distribute — weighted sampling of hidden cards
 * ====================================================================== */
static void _inference_guided_distribute(
    uint8_t *unseen, int unseen_count,
    CallbreakState *state,
    const int *needed,
    int root_idx,
    const InferenceCtx *ctx,
    const int *bids,
    const int *tricks_won
) {
    int total_needed = 0;
    for (int i = 0; i < NUM_PLAYERS; i++) total_needed += needed[i];
    if (total_needed == 0) return;

    if (unseen_count < total_needed) {
        /* Fallback: give whatever we can */
        _shuffle(unseen, unseen_count);
        int ci = 0;
        for (int i = 0; i < NUM_PLAYERS; i++) {
            if (i == root_idx || needed[i] == 0) continue;
            int give = needed[i];
            if (ci + give > unseen_count) give = unseen_count - ci;
            if (give <= 0) continue;
            memcpy(&state->hands[i][state->hand_sizes[i]],
                   &unseen[ci], (size_t)give);
            state->hand_sizes[i] += give;
            ci += give;
        }
        return;
    }

    /* Compute per-(player, card) scores */
    double scores[NUM_PLAYERS * TOTAL_CARDS];
    memset(scores, 0, sizeof(scores));
    double card_strength[TOTAL_CARDS];
    int strong_card_count = 0;
    for (int ci = 0; ci < unseen_count; ci++) {
        card_strength[ci] = _card_strength_value(unseen[ci]);
        if (card_strength[ci] >= INFER_STRONG_CARD_THRESHOLD) {
            strong_card_count++;
        }
    }

    for (int p = 0; p < NUM_PLAYERS; p++) {
        if (p == root_idx) continue;
        if (needed[p] == 0) continue;
        for (int ci = 0; ci < unseen_count; ci++) {
            scores[p * TOTAL_CARDS + ci] = _card_player_compatibility_score(
                unseen[ci], p, state, ctx, bids, tricks_won
            );
        }
    }

    /* Order cards from most constrained to least constrained, then by strength. */
    int card_order[TOTAL_CARDS];
    int eligible_counts[TOTAL_CARDS];
    for (int ci = 0; ci < unseen_count; ci++) {
        int eligible = 0;
        for (int p = 0; p < NUM_PLAYERS; p++) {
            if (p == root_idx || needed[p] == 0) continue;
            if (scores[p * TOTAL_CARDS + ci] > 0.0) eligible++;
        }
        card_order[ci] = ci;
        eligible_counts[ci] = eligible;
    }

    int strong_targets[NUM_PLAYERS];
    double desire_signal[NUM_PLAYERS];
    memset(strong_targets, 0, sizeof(strong_targets));
    memset(desire_signal, 0, sizeof(desire_signal));

    if (total_needed > 0 && strong_card_count > 0) {
        double base_ratio = (double)strong_card_count / (double)total_needed;
        int target_total = strong_card_count;
        if (target_total > total_needed) target_total = total_needed;

        for (int p = 0; p < NUM_PLAYERS; p++) {
            if (p == root_idx || needed[p] == 0) continue;
            desire_signal[p] = _player_strength_desire(p, ctx, bids, tricks_won);
            double target_ratio = _clamp_double(
                base_ratio + desire_signal[p] * INFER_STRONG_TARGET_SHIFT,
                0.0, 1.0
            );
            int target = (int)lround(target_ratio * (double)needed[p]);
            if (target < 0) target = 0;
            if (target > needed[p]) target = needed[p];
            strong_targets[p] = target;
        }

        int current_total = 0;
        for (int p = 0; p < NUM_PLAYERS; p++) current_total += strong_targets[p];

        while (current_total < target_total) {
            int best_p = -1;
            double best_desire = -1e18;
            for (int p = 0; p < NUM_PLAYERS; p++) {
                if (p == root_idx || needed[p] == 0 || strong_targets[p] >= needed[p]) continue;
                if (desire_signal[p] > best_desire) {
                    best_desire = desire_signal[p];
                    best_p = p;
                }
            }
            if (best_p < 0) break;
            strong_targets[best_p]++;
            current_total++;
        }

        while (current_total > target_total) {
            int best_p = -1;
            double lowest_desire = 1e18;
            for (int p = 0; p < NUM_PLAYERS; p++) {
                if (p == root_idx || strong_targets[p] <= 0) continue;
                if (desire_signal[p] < lowest_desire) {
                    lowest_desire = desire_signal[p];
                    best_p = p;
                }
            }
            if (best_p < 0) break;
            strong_targets[best_p]--;
            current_total--;
        }
    }

    for (int i = 0; i < unseen_count - 1; i++) {
        for (int j = i + 1; j < unseen_count; j++) {
            int ci = card_order[i];
            int cj = card_order[j];
            if (eligible_counts[cj] < eligible_counts[ci]
                || (eligible_counts[cj] == eligible_counts[ci]
                    && card_strength[cj] > card_strength[ci])) {
                int tmp = card_order[i];
                card_order[i] = card_order[j];
                card_order[j] = tmp;
            }
        }
    }

    for (int attempt = 0; attempt < INFER_MAX_RETRIES; attempt++) {
        uint8_t temp_hands[NUM_PLAYERS][CARDS_PER_HAND];
        int temp_sizes[NUM_PLAYERS];
        memset(temp_sizes, 0, sizeof(temp_sizes));
        int remaining_slots[NUM_PLAYERS];
        memcpy(remaining_slots, needed, sizeof(remaining_slots));
        int assigned_strong[NUM_PLAYERS];
        memset(assigned_strong, 0, sizeof(assigned_strong));
        uint8_t assigned[TOTAL_CARDS];
        memset(assigned, 0, sizeof(assigned));
        int success = 1;

        for (int order_idx = 0; order_idx < unseen_count; order_idx++) {
            int ci = card_order[order_idx];
            if (assigned[ci]) continue;

            int total_remaining = 0;
            for (int p = 0; p < NUM_PLAYERS; p++) total_remaining += remaining_slots[p];
            if (total_remaining <= 0) break;

            int candidates[NUM_PLAYERS];
            double weights[NUM_PLAYERS];
            int candidate_count = 0;
            double total_weight = 0.0;

            for (int p = 0; p < NUM_PLAYERS; p++) {
                if (p == root_idx || remaining_slots[p] <= 0) continue;

                double w = scores[p * TOTAL_CARDS + ci];
                if (w <= 0.0) continue;
                if (!_assignment_stays_feasible(
                        scores, unseen_count, remaining_slots,
                        assigned, ci, p)) {
                    continue;
                }

                int compatible = _count_remaining_compatible_cards(
                    scores, unseen_count, assigned, p, ci
                );
                double pressure = compatible > 0
                    ? (double)remaining_slots[p] / (double)compatible
                    : (double)remaining_slots[p];
                w *= 1.0 + INFER_SLOT_PRESSURE_WEIGHT * pressure;

                if (card_strength[ci] >= INFER_STRONG_CARD_THRESHOLD) {
                    int target_gap = strong_targets[p] - assigned_strong[p];
                    if (target_gap > 0) {
                        double ratio = (double)target_gap
                            / (double)(remaining_slots[p] > 0 ? remaining_slots[p] : 1);
                        w *= 1.0 + INFER_STRONG_TARGET_BONUS * ratio;
                    } else if (target_gap < 0) {
                        double penalty = 1.0 + INFER_STRONG_TARGET_PENALTY * (double)target_gap;
                        if (penalty < 0.60) penalty = 0.60;
                        w *= penalty;
                    }
                }

                candidates[candidate_count] = p;
                weights[candidate_count] = w;
                total_weight += w;
                candidate_count++;
            }

            if (candidate_count == 0 || total_weight <= 0.0) {
                success = 0;
                break;
            }

            double r = _rand_double() * total_weight;
            double cum = 0.0;
            int chosen_idx = candidate_count - 1;
            for (int i = 0; i < candidate_count; i++) {
                cum += weights[i];
                if (cum >= r) {
                    chosen_idx = i;
                    break;
                }
            }

            int chosen_player = candidates[chosen_idx];
            temp_hands[chosen_player][temp_sizes[chosen_player]++] = unseen[ci];
            remaining_slots[chosen_player]--;
            if (card_strength[ci] >= INFER_STRONG_CARD_THRESHOLD) {
                assigned_strong[chosen_player]++;
            }
            assigned[ci] = 1;
        }

        for (int p = 0; p < NUM_PLAYERS; p++) {
            if (remaining_slots[p] != 0) {
                success = 0;
                break;
            }
        }

        if (success) {
            for (int i = 0; i < NUM_PLAYERS; i++) {
                if (i == root_idx || needed[i] == 0) continue;
                memcpy(state->hands[i], temp_hands[i],
                       (size_t)temp_sizes[i]);
                state->hand_sizes[i] = temp_sizes[i];
            }
            return;
        }

        for (int i = unseen_count - 1; i > 0; i--) {
            int j = _rand_int(i + 1);
            int tmp = card_order[i];
            card_order[i] = card_order[j];
            card_order[j] = tmp;
        }
    }

    /* Fallback to a legal random assignment that still respects all hard constraints. */
    if (_legal_random_distribute_from_scores(scores, unseen, unseen_count, state, needed, root_idx)) {
        return;
    }

    /* Last resort: keep at least void constraints if even the legal fallback cannot fit. */
    uint8_t combined_voids[NUM_PLAYERS];
    for (int i = 0; i < NUM_PLAYERS; i++) {
        combined_voids[i] = state->void_suits_by_player[i] | ctx->void_suits[i];
    }
    _distribute_hidden_cards(unseen, unseen_count, state, needed,
                             root_idx, combined_voids);
}
/* ======================================================================
 * Run one determinization 
 * ====================================================================== */
static void _run_one_det(
    const uint8_t *unseen_base, int unseen_count,
    const uint8_t *known_hand, int known_hand_size,
    const CallbreakState *root_state,
    int player_index,
    const uint8_t *legal, int legal_count,
    int sims_per_det,
    const SearchParams *params,
    const int *opponent_card_counts,
    const InferenceCtx *infer_ctx,
    /* output: accumulated stats */
    int *action_visits,       /* [legal_count] */
    double *action_rewards    /* [legal_count] */
) {
    (void)sims_per_det;
    /* Build base state for this determinization */
    CallbreakState base;
    _state_copy(&base, root_state);

    /* Copy known hand */
    memcpy(base.hands[player_index], known_hand, (size_t)known_hand_size);
    base.hand_sizes[player_index] = known_hand_size;

    /* Set up opponent needs */
    int needed[NUM_PLAYERS];
    memset(needed, 0, sizeof(needed));
    for (int i = 0; i < NUM_PLAYERS; i++) {
        if (i == player_index) continue;
        base.hand_sizes[i] = 0;
        needed[i] = opponent_card_counts[i];
    }

    /* Distribute unseen cards using inference-guided determinization */
    uint8_t unseen_copy[TOTAL_CARDS];
    memcpy(unseen_copy, unseen_base, (size_t)unseen_count);
    _inference_guided_distribute(unseen_copy, unseen_count, &base, needed,
                                 player_index, infer_ctx, root_state->bids, root_state->tricks_won);

    /* Sort all hands */
    for (int i = 0; i < NUM_PLAYERS; i++) {
        _sort_hand(base.hands[i], base.hand_sizes[i]);
    }

    /* PIMC Phase: Evaluate EVERY legal action EXACTLY ONCE for this determinized world */
    for (int action_idx = 0; action_idx < legal_count; action_idx++) {
        CallbreakState rollout_state;
        _state_copy(&rollout_state, &base);
        _state_play_card_inplace(&rollout_state, legal[action_idx]);

        double reward = bot_logic_rollout(&rollout_state, player_index,
                                          params->block_leader, params->cumulative_scores,
                                          params->human_index,
                                          params->current_round,
                                          params->total_rounds);

        /* Aggregate directly into global stats */
        action_visits[action_idx]++;
        action_rewards[action_idx] += reward;
    }
}

void determinize_hidden_hands_c(
    const uint8_t *original_deck,
    const uint8_t *known_hand,
    int known_hand_size,
    const int *bids,
    const int *tricks_won,
    int current_turn,
    const uint8_t *cards_played,
    int cards_played_count,
    int trick_starter,
    int dealer_index,
    const uint8_t *discard_pile,
    int discard_count,
    int led_suit,
    const uint8_t *void_tracker,
    const int *discard_starters,
    int discard_trick_count,
    int player_index,
    const int *player_in_game_ids,
    uint8_t *out_hands,
    int *out_hand_sizes
) {
    _ensure_rng();

    uint8_t unseen[TOTAL_CARDS];
    int unseen_count = 0;
    for (int i = 0; i < TOTAL_CARDS; i++) {
        uint8_t c = original_deck[i];
        if (_card_in(c, discard_pile, discard_count)) continue;
        if (_card_in(c, known_hand, known_hand_size)) continue;
        if (_card_in(c, cards_played, cards_played_count)) continue;
        unseen[unseen_count++] = c;
    }

    int complete_tricks = 0;
    for (int i = 0; i < NUM_PLAYERS; i++) complete_tricks += tricks_won[i];
    int total_cards_played = complete_tricks * NUM_PLAYERS + cards_played_count;
    if (total_cards_played < 0) total_cards_played = 0;
    if (total_cards_played > TOTAL_CARDS) total_cards_played = TOTAL_CARDS;

    int opponent_card_counts[NUM_PLAYERS];
    for (int i = 0; i < NUM_PLAYERS; i++) {
        if (i == player_index) {
            opponent_card_counts[i] = 0;
            continue;
        }
        int played_in_trick = 0;
        for (int j = 0; j < cards_played_count; j++) {
            if ((trick_starter + j) % NUM_PLAYERS == i) {
                played_in_trick = 1;
                break;
            }
        }
        opponent_card_counts[i] = CARDS_PER_HAND - complete_tricks - played_in_trick;
        if (opponent_card_counts[i] < 0) opponent_card_counts[i] = 0;
    }

    InferenceCtx infer_ctx;
    _build_inference_ctx(
        &infer_ctx,
        discard_pile, discard_count,
        discard_starters, discard_trick_count,
        cards_played, cards_played_count,
        trick_starter,
        void_tracker,
        player_in_game_ids,
        player_index, dealer_index,
        opponent_card_counts, known_hand_size
    );

    CallbreakState base;
    memset(&base, 0, sizeof(base));
    if (player_in_game_ids) {
        memcpy(base.in_game_ids, player_in_game_ids, sizeof(base.in_game_ids));
    } else {
        for (int i = 0; i < NUM_PLAYERS; i++) base.in_game_ids[i] = i;
    }

    for (int i = 0; i < NUM_PLAYERS; i++) {
        base.played_card_counts[i] = infer_ctx.played_card_counts[i];
        memcpy(base.played_cards_by_player[i], infer_ctx.played_cards_by_player[i], CARDS_PER_HAND);
        base.void_suits_by_player[i] = infer_ctx.void_suits[i];
        base.remaining_card_counts[i] = infer_ctx.remaining_card_counts[i];
    }
    base.completed_tricks_count = infer_ctx.trick_count;
    for (int t = 0; t < infer_ctx.trick_count; t++) {
        base.trick_history[t] = infer_ctx.tricks[t];
    }

    memcpy(base.bids, bids, sizeof(int) * NUM_PLAYERS);
    memcpy(base.tricks_won, tricks_won, sizeof(int) * NUM_PLAYERS);
    base.current_turn = current_turn;
    if (cards_played_count > 0) {
        memcpy(base.cards_played, cards_played, (size_t)cards_played_count);
    }
    base.cards_played_count = cards_played_count;
    base.trick_starter = trick_starter;
    base.dealer_index = dealer_index;
    base.led_suit = led_suit;
    base.total_cards_played = total_cards_played;

    memcpy(base.hands[player_index], known_hand, (size_t)known_hand_size);
    base.hand_sizes[player_index] = known_hand_size;

    int needed[NUM_PLAYERS];
    memset(needed, 0, sizeof(needed));
    for (int i = 0; i < NUM_PLAYERS; i++) {
        if (i == player_index) continue;
        base.hand_sizes[i] = 0;
        needed[i] = opponent_card_counts[i];
    }

    uint8_t unseen_copy[TOTAL_CARDS];
    memcpy(unseen_copy, unseen, (size_t)unseen_count);
    _inference_guided_distribute(unseen_copy, unseen_count, &base, needed,
                                 player_index, &infer_ctx, bids, tricks_won);

    for (int i = 0; i < NUM_PLAYERS; i++) {
        _sort_hand(base.hands[i], base.hand_sizes[i]);
        out_hand_sizes[i] = base.hand_sizes[i];
        memcpy(&out_hands[i * CARDS_PER_HAND], base.hands[i], CARDS_PER_HAND);
    }
}

void debug_inference_snapshot_c(
    const uint8_t *known_hand,
    int known_hand_size,
    const int *tricks_won,
    const uint8_t *cards_played,
    int cards_played_count,
    int trick_starter,
    int dealer_index,
    const uint8_t *discard_pile,
    int discard_count,
    const uint8_t *void_tracker,
    const int *discard_starters,
    int discard_trick_count,
    int player_index,
    const int *player_in_game_ids,
    uint8_t *out_voids,
    int *out_max_ranks
) {
    (void)known_hand;
    _ensure_rng();

    int complete_tricks = 0;
    for (int i = 0; i < NUM_PLAYERS; i++) complete_tricks += tricks_won[i];

    int opponent_card_counts[NUM_PLAYERS];
    for (int i = 0; i < NUM_PLAYERS; i++) {
        if (i == player_index) {
            opponent_card_counts[i] = 0;
            continue;
        }
        int played_in_trick = 0;
        for (int j = 0; j < cards_played_count; j++) {
            if ((trick_starter + j) % NUM_PLAYERS == i) {
                played_in_trick = 1;
                break;
            }
        }
        opponent_card_counts[i] = CARDS_PER_HAND - complete_tricks - played_in_trick;
        if (opponent_card_counts[i] < 0) opponent_card_counts[i] = 0;
    }

    InferenceCtx infer_ctx;
    _build_inference_ctx(
        &infer_ctx,
        discard_pile, discard_count,
        discard_starters, discard_trick_count,
        cards_played, cards_played_count,
        trick_starter,
        void_tracker,
        player_in_game_ids,
        player_index, dealer_index,
        opponent_card_counts, known_hand_size
    );

    for (int i = 0; i < NUM_PLAYERS; i++) {
        out_voids[i] = infer_ctx.void_suits[i];
        for (int s = 0; s < NUM_SUITS; s++) {
            out_max_ranks[i * NUM_SUITS + s] = infer_ctx.max_rank[i][s];
        }
    }
}

/* ======================================================================
 * mcts_search_c — main entry point, port of mcts_search
 * ====================================================================== */
void mcts_search_c(
    const uint8_t *original_deck,
    const uint8_t *known_hand,
    int known_hand_size,
    const int *bids,
    const int *tricks_won,
    int current_turn,
    const uint8_t *cards_played,
    int cards_played_count,
    int trick_starter,
    int dealer_index,
    const uint8_t *discard_pile,
    int discard_count,
    int led_suit,
    const uint8_t *void_tracker,
    const int *discard_starters,
    int discard_trick_count,
    int player_index,
    const SearchParams *params,
    SearchResult *result
) {
    _ensure_rng();

    /* Compute legal moves */
    uint8_t legal[MAX_LEGAL];
    int legal_count = get_legal_cards_c(
        known_hand, known_hand_size,
        cards_played, cards_played_count,
        led_suit, legal
    );


    if (legal_count <= 0) {
        result->best_card = CARD_NONE;
        result->num_actions = 0;
        return;
    }

    if (legal_count == 1) {
        result->best_card = legal[0];
        result->num_actions = 1;
        result->actions[0].card = legal[0];
        result->actions[0].visits = 1;
        result->actions[0].total_reward = 1.0;
        result->actions[0].avg = 1.0;
        return;
    }

    /* Compute unseen cards */
    uint8_t unseen[TOTAL_CARDS];
    int unseen_count = 0;
    for (int i = 0; i < TOTAL_CARDS; i++) {
        uint8_t c = original_deck[i];
        if (_card_in(c, discard_pile, discard_count)) continue;
        if (_card_in(c, known_hand, known_hand_size)) continue;
        if (_card_in(c, cards_played, cards_played_count)) continue;
        unseen[unseen_count++] = c;
    }

    /* Compute progress from authoritative trick state */
    int complete_tricks = 0;
    for (int i = 0; i < NUM_PLAYERS; i++) complete_tricks += tricks_won[i];
    int total_cards_played = complete_tricks * NUM_PLAYERS + cards_played_count;
    if (total_cards_played < 0) total_cards_played = 0;
    if (total_cards_played > TOTAL_CARDS) total_cards_played = TOTAL_CARDS;

    /* Compute correct opponent hand sizes */
    int opponent_card_counts[NUM_PLAYERS];
    for (int i = 0; i < NUM_PLAYERS; i++) {
        if (i == player_index) {
            opponent_card_counts[i] = 0;
            continue;
        }
        int played_in_trick = 0;
        for (int j = 0; j < cards_played_count; j++) {
            if ((trick_starter + j) % NUM_PLAYERS == i) {
                played_in_trick = 1;
                break;
            }
        }
        opponent_card_counts[i] = CARDS_PER_HAND - complete_tricks - played_in_trick;
        if (opponent_card_counts[i] < 0) opponent_card_counts[i] = 0;
    }
    int total_needed = 0;
    for (int i = 0; i < NUM_PLAYERS; i++) total_needed += opponent_card_counts[i];
    int root_played_in_trick = 0;
    for (int j = 0; j < cards_played_count; j++) {
        if ((trick_starter + j) % NUM_PLAYERS == player_index) {
            root_played_in_trick = 1;
            break;
        }
    }
    int expected_root_hand_size = CARDS_PER_HAND - complete_tricks - root_played_in_trick;
    int skip_search = 0;
    if (expected_root_hand_size < 0) expected_root_hand_size = 0;
    if (known_hand_size != expected_root_hand_size) skip_search = 1;
    if (unseen_count != total_needed) skip_search = 1;

    /* Accumulated action stats */
    int    action_visits[MAX_LEGAL];
    double action_rewards[MAX_LEGAL];
    memset(action_visits, 0, sizeof(int) * (size_t)legal_count);
    memset(action_rewards, 0, sizeof(double) * (size_t)legal_count);

    /* Build inference context from trick history */
    InferenceCtx infer_ctx;
    _build_inference_ctx(
        &infer_ctx,
        discard_pile, discard_count,
        discard_starters, discard_trick_count,
        cards_played, cards_played_count,
        trick_starter,
        void_tracker,
        params->player_in_game_ids,
        player_index, dealer_index,
        opponent_card_counts, known_hand_size
    );

    /* Build root_state to pass into determinization */
    CallbreakState root_state;
    memset(&root_state, 0, sizeof(root_state));
    memcpy(root_state.in_game_ids, params->player_in_game_ids, sizeof(root_state.in_game_ids));

    /* Initialize history from infer_ctx */
    for (int i = 0; i < NUM_PLAYERS; i++) {
        root_state.played_card_counts[i] = infer_ctx.played_card_counts[i];
        memcpy(root_state.played_cards_by_player[i], infer_ctx.played_cards_by_player[i], CARDS_PER_HAND);
        root_state.void_suits_by_player[i] = infer_ctx.void_suits[i];
        root_state.remaining_card_counts[i] = infer_ctx.remaining_card_counts[i];
    }
    root_state.completed_tricks_count = infer_ctx.trick_count;
    for (int t = 0; t < infer_ctx.trick_count; t++) {
        root_state.trick_history[t] = infer_ctx.tricks[t];
    }
    
    memcpy(root_state.bids, bids, sizeof(int) * NUM_PLAYERS);
    memcpy(root_state.tricks_won, tricks_won, sizeof(int) * NUM_PLAYERS);
    root_state.current_turn = current_turn;
    if (cards_played_count > 0) {
        memcpy(root_state.cards_played, cards_played, (size_t)cards_played_count);
    }
    root_state.cards_played_count = cards_played_count;
    root_state.trick_starter = trick_starter;
    root_state.dealer_index = dealer_index;
    root_state.led_suit = led_suit;
    root_state.total_cards_played = total_cards_played;
    memcpy(root_state.hands[player_index], known_hand, (size_t)known_hand_size);
    root_state.hand_sizes[player_index] = known_hand_size;

    /* Flat PIMC only determinizes at the current root state, so adapt effort
     * from the current hidden-state size rather than trying to re-determinize
     * inside the deeper rollout itself. */
    double effort_scale = _compute_effort_scale(opponent_card_counts, unseen_count);

    /* PIMC CPU budget: maintain a scaled total rollout target. */
    int base_total_rollouts = params->iterations * params->sims_per_det;
    if (base_total_rollouts < legal_count) base_total_rollouts = legal_count;
    int target_total_rollouts = (int)lround((double)base_total_rollouts * effort_scale);
    if (target_total_rollouts < legal_count) target_total_rollouts = legal_count;

    int iterations = (target_total_rollouts + legal_count - 1)
                   / (legal_count > 0 ? legal_count : 1);
    if (iterations < 1) iterations = 1;

    int sims_per_det = 1; /* Unused now, but kept for signature */

    if (!skip_search && params->time_limit_ms > 0) {
        /* Time-based search */
        struct timespec start_ts, now_ts;
        clock_gettime(CLOCK_MONOTONIC, &start_ts);
        int effective_time_limit_ms = (int)lround((double)params->time_limit_ms * effort_scale);
        if (effective_time_limit_ms < 1) effective_time_limit_ms = 1;
        double deadline_sec = (double)effective_time_limit_ms / 1000.0;

        while (1) {
            _run_one_det(
                unseen, unseen_count,
                known_hand, known_hand_size,
                &root_state,
                player_index, legal, legal_count,
                sims_per_det, params,
                opponent_card_counts,
                &infer_ctx,
                action_visits, action_rewards
            );

            clock_gettime(CLOCK_MONOTONIC, &now_ts);
            double elapsed = (double)(now_ts.tv_sec - start_ts.tv_sec)
                           + (double)(now_ts.tv_nsec - start_ts.tv_nsec) / 1e9;
            if (elapsed >= deadline_sec) break;
        }
    } else if (!skip_search) {
        /* Iteration-based search */
        for (int det = 0; det < iterations; det++) {
            _run_one_det(
                unseen, unseen_count,
                known_hand, known_hand_size,
                &root_state,
                player_index, legal, legal_count,
                sims_per_det, params,
                opponent_card_counts,
                &infer_ctx,
                action_visits, action_rewards
            );
        }
    }

    /* Compute elite rule-based prior as tiebreaker (resolves EV collisions intelligently) */
    BotLogicC tiebreak_bl;
    /* Bug fix: use known_hand directly — root_state.hands[player_index] is never populated */
    bl_init(&tiebreak_bl, known_hand, known_hand_size,
            bids[player_index], tricks_won[player_index], player_index);

    /* Replay historical discard_pile to update unrevealed tracking for the tiebreaker */
    int hist_trick_starter = (dealer_index + 1) % 4;
    int current_play_turn = 0;
    if (discard_pile && discard_count > 0) {
        int num_hist_tricks = discard_count / 4;
        if (discard_trick_count > 0 && discard_trick_count < num_hist_tricks)
            num_hist_tricks = discard_trick_count;
        current_play_turn = num_hist_tricks;

        for (int t = 0; t < num_hist_tricks; t++) {
            const uint8_t *trick_cards = &discard_pile[t * 4];
            /* Bug fix: use authoritative discard_starters when available */
            int starter = (discard_starters && t < discard_trick_count)
                        ? discard_starters[t] : hist_trick_starter;

            for (int tt = 0; tt < 4; tt++) {
                int p = (starter + tt) % 4;
                bl_on_card_selected(&tiebreak_bl, trick_cards[tt], tt, p == player_index);
            }
            /* Bug fix: notify trick completion so cut_trick_played flags are set */
            bl_on_trick_completed(&tiebreak_bl, trick_cards, 4, starter);

            int wi = _get_winning_index(trick_cards, 4);
            hist_trick_starter = (starter + wi) % 4;
        }
    }

    /* Replay partial trick for accurate tiebreaker context */
    for (int tt = 0; tt < cards_played_count; tt++) {
        int p = (trick_starter + tt) % 4;
        bl_on_card_selected(&tiebreak_bl, cards_played[tt], tt, p == player_index);
    }
    
    /* Match the original bot logic: opening strategies are only created on the actual play turn. */
    bl_on_throw_turn_started(&tiebreak_bl, current_play_turn);
    
    /* Sync legal cards */
    memcpy(tiebreak_bl.legal, legal, (size_t)legal_count);
    tiebreak_bl.legal_count = legal_count;
    
    int throw_turn = cards_played_count;
    uint8_t best_heuristic_card = bl_select_card(&tiebreak_bl, throw_turn,
                                                 cards_played, cards_played_count,
                                                 dealer_index, bids);

    /* Build result */
    result->num_actions = legal_count;
    
    int best_h_idx = 0;
    for (int i = 0; i < legal_count; i++) {
        result->actions[i].card = legal[i];
        result->actions[i].visits = action_visits[i];
        result->actions[i].total_reward = action_rewards[i];
        result->actions[i].avg = (action_visits[i] > 0)
            ? action_rewards[i] / (double)action_visits[i] : 0.0;

        if (legal[i] == best_heuristic_card) {
            best_h_idx = i;
        }
    }

    /* PIMC Confidence Margin */
    double confidence_margin = _adaptive_confidence_margin(params, unseen_count);

    int best_idx = best_h_idx;
    double best_avg = result->actions[best_h_idx].avg;

    for (int i = 0; i < legal_count; i++) {
        if (i == best_h_idx) continue;
        
        /* Select action that clearly beats the baseline EV by confidence margin */
        if (action_visits[i] > 0
            && result->actions[i].avg > best_avg + confidence_margin) {
            best_avg = result->actions[i].avg;
            best_idx = i;
        }
    }

    result->best_card = legal[best_idx];
}

/* ======================================================================
 * bot_logic_select_card_c — C API to invoke bot_logic rule-based selection.
 * ====================================================================== */
__attribute__((visibility("default"))) uint8_t bot_logic_select_card_c(
    const uint8_t *known_hand, int known_hand_size,
    const int *bids, const int *tricks_won,
    int current_turn,
    const uint8_t *cards_played, int cards_played_count,
    int trick_starter, int dealer_index,
    const uint8_t *discard_pile, int discard_count,
    const int *discard_starters, int discard_trick_count,
    int led_suit,
    int player_index
) {
    _ensure_rng();
    (void)current_turn;

    BotLogicC bl;
    bl_init(&bl, known_hand, known_hand_size, bids[player_index], tricks_won[player_index], player_index);

    /* Replay historical discard pile to update unrevealed tracking and strategies */
    int hist_trick_starter = (dealer_index + 1) % 4;
    int current_play_turn = 0;
    if (discard_pile && discard_count > 0) {
        int num_tricks = discard_count / 4;
        if (discard_trick_count > 0 && discard_trick_count < num_tricks) {
            num_tricks = discard_trick_count;
        }
        current_play_turn = num_tricks;

        for (int t = 0; t < num_tricks; t++) {
            const uint8_t *trick_cards = &discard_pile[t * 4];
            int starter = (discard_starters && t < discard_trick_count) ? discard_starters[t] : hist_trick_starter;
            
            for (int tt = 0; tt < 4; tt++) {
                int p = (starter + tt) % 4;
                bl_on_card_selected(&bl, trick_cards[tt], tt, player_index == p);
            }
            
            bl_on_trick_completed(&bl, trick_cards, 4, starter);
            
            /* Estimate next starter if discard_starters not perfectly available */
            int wi = _get_winning_index(trick_cards, 4);
            hist_trick_starter = (starter + wi) % 4;
        }
    }

    /* Set legal cards for the current trick */
    uint8_t legal[MAX_LEGAL];
    int legal_count = get_legal_cards_c(known_hand, known_hand_size,
                                        cards_played, cards_played_count,
                                        led_suit, legal);

    if (legal_count > 0) {
        memcpy(bl.legal, legal, (size_t)legal_count);
        bl.legal_count = legal_count;
    } else {
        /* Fallback if logic fails (should not happen with valid input) */
        bl.legal_count = known_hand_size;
        memcpy(bl.legal, known_hand, (size_t)known_hand_size);
    }

    /* Replay partial trick cards for events */
    for (int tt = 0; tt < cards_played_count; tt++) {
        int p = (trick_starter + tt) % 4;
        bl_on_card_selected(&bl, cards_played[tt], tt, player_index == p);
    }

    /* Match the original bot logic: only treat this as opening play when no trick has completed yet. */
    bl_on_throw_turn_started(&bl, current_play_turn);

    return bl_select_card(&bl, cards_played_count, cards_played, cards_played_count, dealer_index, bids);
}
