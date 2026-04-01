/*
 * bot_logic_c.c — C port of bot_logic.py for MCTS rollouts.
 * Rule-based card selection for Callbreak, used during MCTS rollout phase.
 */

#include "bot_logic_c.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

/* ======================================================================
 * Probability tables (ported from bot_logic_prob.gd / BotLogic.gd)
 * ====================================================================== */
static const double PROB_DIST[14][6] = {
    /* my_total_cards = 0..13, opp_at_least = 0..5 (0 unused) */
    {0, 0.995883, 0.949609, 0.727636, 0.246271, 0.0},
    {0, 0.992759, 0.915651, 0.603479, 0.093717, 0.0},
    {0, 0.986198, 0.862628, 0.450359, 0.0,      0.0},
    {0, 0.974804, 0.783795, 0.276870, 0.0,      0.0},
    {0, 0.956055, 0.672427, 0.110936, 0.0,      0.0},
    {0, 0.924156, 0.524086, 0.0,      0.0,      0.0},
    {0, 0.872706, 0.339533, 0.0,      0.0,      0.0},
    {0, 0.790307, 0.145071, 0.0,      0.0,      0.0},
    {0, 0.668897, 0.0,      0.0,      0.0,      0.0},
    {0, 0.504717, 0.0,      0.0,      0.0,      0.0},
    {0, 0.200000, 0.0,      0.0,      0.0,      0.0},
    {0, 0.0,      0.0,      0.0,      0.0,      0.0},
    {0, 0.0,      0.0,      0.0,      0.0,      0.0},
    {0, 0.0,      0.0,      0.0,      0.0,      0.0},
};

static const double SPADE_PROBS[15] = {
    0.0, /* 0 */
    0.0, /* 1 */
    0.030173, /* 2 */
    0.050906, /* 3 */
    0.070872, /* 4 */
    0.091100, /* 5 */
    0.126819, /* 6 */
    0.177419, /* 7 */
    0.232700, /* 8 */
    0.225944, /* 9 */
    0.281416, /* 10 */
    0.4391785, /* 11 */
    0.5414505, /* 12 */
    0.6485445, /* 13 */
    1.0, /* 14 */
};

/* ======================================================================
 * Forward declarations for RNG (defined in mcts_engine.c)
 * ====================================================================== */
extern int _rand_int(int n);
extern double _rand_double(void);

/* ======================================================================
 * Internal helpers
 * ====================================================================== */

static int _rank_in(int rank, const int *arr, int count) {
    for (int i = 0; i < count; i++)
        if (arr[i] == rank) return 1;
    return 0;
}

static int _min_rank(const int *arr, int count) {
    if (count <= 0) return 0;
    int m = arr[0];
    for (int i = 1; i < count; i++)
        if (arr[i] < m) m = arr[i];
    return m;
}

static int _max_rank(const int *arr, int count) {
    if (count <= 0) return 0;
    int m = arr[0];
    for (int i = 1; i < count; i++)
        if (arr[i] > m) m = arr[i];
    return m;
}

static int _2nd_largest(const int *arr, int count) {
    if (count < 2) return arr[0];
    int max1 = -1, max2 = -1;
    for (int i = 0; i < count; i++) {
        if (arr[i] > max1) { max2 = max1; max1 = arr[i]; }
        else if (arr[i] > max2) max2 = arr[i];
    }
    return max2;
}

/* Senior card = highest unrevealed rank in suit */
static int _senior(const BotLogicC *bl, int suit) {
    if (bl->unrevealed_counts[suit] > 0)
        return bl->unrevealed[suit][0]; /* sorted desc */
    return 0;
}

/* Largest rank in player's cards for a suit */
static int _largest_my(const BotLogicC *bl, int suit) {
    if (bl->suit_counts[suit] > 0)
        return bl->suit_ranks[suit][0]; /* sorted desc */
    return 0;
}

static int _has_ace(const int *arr, int count) { return _rank_in(14, arr, count); }
static int _has_king(const int *arr, int count) { return _rank_in(13, arr, count); }
static int _has_queen(const int *arr, int count) { return _rank_in(12, arr, count); }

static double _prob_at_least(int opp_at_least, int my_total_cards) {
    if (my_total_cards < 0 || my_total_cards > 13) return 0.0;
    if (opp_at_least < 0 || opp_at_least > 5) return 0.0;
    return PROB_DIST[my_total_cards][opp_at_least];
}

static double _evaluate_spare_spades(const BotLogicC *bl) {
    int *sp = (int *)bl->suit_ranks[SUIT_SPADES];
    int spn = bl->suit_counts[SUIT_SPADES];
    double spare = 1.0;
    if (_rank_in(14, sp, spn)) {
        spare = 0.75;
        if (_rank_in(13, sp, spn)) {
            spare = 0.5;
            if (_rank_in(12, sp, spn)) {
                spare = 0.25;
                if (_rank_in(11, sp, spn)) {
                    spare = 0.0;
                }
            }
        }
    }
    return spare;
}



static double _calculate_cut_score(int suit_count) {
    double cut = 0.0;
    for (int atleast = 1; atleast <= 5; atleast++) {
        if (atleast <= suit_count) continue;
        double v = _prob_at_least(atleast, suit_count);
        if (v != 0.0) {
            cut += v;
            if (suit_count == 2 && atleast == 3) cut += 0.2;
        }
    }
    return cut;
}

static int _opponent_higher_spades_count(int rank, const int *my_spades, int spn) {
    int count = 0;
    for (int r = 14; r > rank; r--) {
        if (_rank_in(r, my_spades, spn)) continue;
        count++;
    }
    return count;
}

static int _opponent_higher_from_remaining(int rank, const int *opp_rem, int opp_count) {
    int count = 0;
    for (int i = 0; i < opp_count; i++) {
        if (opp_rem[i] > rank) count++;
    }
    return count;
}

static void _calculate_spades_score(
    const BotLogicC *bl,
    double total_cut_score,
    double *confirm_out,
    double *projected_out
) {
    const int *sp = bl->suit_ranks[SUIT_SPADES];
    int spn = bl->suit_counts[SUIT_SPADES];

    double total_spades_score = 0.0;

    /* m_my_remaining_spades := p_my_spades_cards.duplicate() */
    int remaining[13];
    int remaining_count = spn;
    for (int i = 0; i < spn; i++) remaining[i] = sp[i];

    for (int i = 0; i < spn; i++) {
        int card = sp[i];
        int opp_higher = _opponent_higher_spades_count(card, sp, spn);
        if (opp_higher == 0) {
            total_spades_score += 1.0;
            /* remove index 0 */
            if (remaining_count > 0) {
                for (int j = 0; j < remaining_count - 1; j++)
                    remaining[j] = remaining[j + 1];
                remaining_count--;
            }
        }
    }

    double confirm_score = total_spades_score;

    /* opp_remaining_spades: ranks 14..2 not in my spades */
    int opp_rem[13];
    int opp_count = 0;
    for (int r = 14; r >= 2; r--) {
        if (_rank_in(r, sp, spn)) continue;
        opp_rem[opp_count++] = r;
    }

    for (int i = 0; i < remaining_count; i++) {
        if (_opponent_higher_from_remaining(remaining[i], opp_rem, opp_count) >= 1) {
            remaining[i] = 0;
            /* opp_remaining_spades.remove(0) */
            if (opp_count > 0) {
                for (int j = 0; j < opp_count - 1; j++)
                    opp_rem[j] = opp_rem[j + 1];
                opp_count--;
            }
        }
    }

    int zeros = 0;
    for (int i = 0; i < remaining_count; i++)
        if (remaining[i] == 0) zeros++;

    confirm_score += (double)(remaining_count - zeros);
    total_spades_score = confirm_score;

    if (total_cut_score + confirm_score < (double)spn - bl->spare_spades) {
        int start = (int)confirm_score;
        int cut_round = (int)(total_cut_score + 0.5);
        int end = spn - cut_round;
        if (start < 0) start = 0;
        if (end > spn) end = spn;
        for (int i = start; i < end; i++) {
            int rank = sp[i];
            if (rank >= 2 && rank <= 14)
                total_spades_score += SPADE_PROBS[rank];
        }
    }

    if ((int)confirm_score == spn)
        total_spades_score = confirm_score;

    *confirm_out = confirm_score;
    *projected_out = total_spades_score;
}

/* Check if all legal cards are spades */
static int _all_spades_legal(const BotLogicC *bl) {
    for (int i = 0; i < bl->legal_count; i++)
        if (CARD_SUIT(bl->legal[i]) != SUIT_SPADES) return 0;
    return 1;
}

/* Smallest rank from legal cards (excluding spades unless all spades) */
static int _smallest_legal_rank(const BotLogicC *bl) {
    int all_sp = _all_spades_legal(bl);
    int min_r = 99;
    for (int i = 0; i < bl->legal_count; i++) {
        if (!all_sp && CARD_SUIT(bl->legal[i]) == SUIT_SPADES) continue;
        int r = CARD_RANK(bl->legal[i]);
        if (r < min_r) min_r = r;
    }
    return (min_r == 99) ? 2 : min_r;
}

/* Validate a card is in the legal set; if not, return best fallback */
static uint8_t _validate_legal(const BotLogicC *bl, uint8_t card) {
    for (int i = 0; i < bl->legal_count; i++)
        if (bl->legal[i] == card) return card;
    /* Fallback: try same suit as intended card */
    int suit = CARD_SUIT(card);
    uint8_t fallback = CARD_NONE;
    int fb_rank = 99;
    for (int i = 0; i < bl->legal_count; i++) {
        if (CARD_SUIT(bl->legal[i]) == suit) {
            int r = CARD_RANK(bl->legal[i]);
            if (r < fb_rank) { fb_rank = r; fallback = bl->legal[i]; }
        }
    }
    if (fallback != CARD_NONE) return fallback;
    /* Last resort: any legal card */
    return bl->legal[0];
}

/* Get winning card from trick */
static uint8_t _winning_card(const uint8_t *played, int count) {
    if (count == 0) return CARD_NONE;
    int led = CARD_SUIT(played[0]);
    uint8_t best = played[0];
    int best_rank = CARD_RANK(played[0]);
    int best_suit = led;
    for (int i = 1; i < count; i++) {
        int cs = CARD_SUIT(played[i]);
        int cr = CARD_RANK(played[i]);
        if (cs == SUIT_SPADES && best_suit != SUIT_SPADES) {
            best = played[i]; best_rank = cr; best_suit = cs;
        } else if (cs == best_suit && cr > best_rank) {
            best = played[i]; best_rank = cr;
        }
    }
    return best;
}

/* Remove rank from sorted desc array, returns new count */
static int _remove_rank(int *arr, int count, int rank) {
    for (int i = 0; i < count; i++) {
        if (arr[i] == rank) {
            for (int j = i; j < count - 1; j++) arr[j] = arr[j+1];
            return count - 1;
        }
    }
    return count;
}



/* ======================================================================
 * Random card selection (port of _get_random_card)
 * ====================================================================== */
static uint8_t _random_card(BotLogicC *bl, const uint8_t *cards, int count) {
    if (count <= 0) return CARD_NONE;
    uint8_t card = cards[_rand_int(count)];
    int suit = CARD_SUIT(card);
    int rank = CARD_RANK(card);
    if (bl->unrevealed_counts[suit] == 1) return card;

    int retries = 0;
    while (bl->unrevealed_counts[suit] > 1
           && bl->unrevealed[suit][1] == rank) {
        if (count == 1 || retries >= 20) break;
        card = cards[_rand_int(count)];
        suit = CARD_SUIT(card);
        rank = CARD_RANK(card);
        retries++;
        if (bl->unrevealed_counts[suit] == 1) return card;
    }
    return card;
}

/* ======================================================================
 * Confuser card (port of get_confuser_card)
 * Now validates output against legal cards.
 * ====================================================================== */
static uint8_t _confuser(BotLogicC *bl, int smallest_rank, int suit) {
    int confuser = smallest_rank;
    if (bl->suit_counts[suit] == 0)
        return _validate_legal(bl, MAKE_CARD(confuser, suit));
    int highest = bl->suit_ranks[suit][0]; /* sorted desc */
    for (int rank = smallest_rank + 1; rank <= highest; rank++) {
        if (_rank_in(rank, bl->unrevealed[suit], bl->unrevealed_counts[suit])) {
            if (_rank_in(rank, bl->suit_ranks[suit], bl->suit_counts[suit]))
                confuser = rank;
            else
                return _validate_legal(bl, MAKE_CARD(confuser, suit));
        }
    }
    return _validate_legal(bl, MAKE_CARD(confuser, suit));
}

/* ======================================================================
 * Cut chance but no spades (port of _on_cut_chance_but_have_no_spades)
 * Pick smallest card from the longest non-trump suit.
 * ====================================================================== */
static uint8_t _cut_no_spades(BotLogicC *bl) {
    int max_size = 0;
    int cands[3], cc = 0;
    for (int s = SUIT_DIAMONDS; s <= SUIT_HEARTS; s++) {
        if (bl->suit_counts[s] > max_size) {
            max_size = bl->suit_counts[s];
            cc = 0; cands[cc++] = s;
        } else if (bl->suit_counts[s] == max_size && max_size > 0) {
            cands[cc++] = s;
        }
    }
    if (cc == 0) return bl->legal[0]; /* fallback to any legal card */
    /* Pick suit with smallest min rank */
    int best_s = cands[0];
    int best_min = _min_rank(bl->suit_ranks[cands[0]], bl->suit_counts[cands[0]]);
    for (int i = 1; i < cc; i++) {
        int mr = _min_rank(bl->suit_ranks[cands[i]], bl->suit_counts[cands[i]]);
        if (mr < best_min) { best_min = mr; best_s = cands[i]; }
    }
    return _validate_legal(bl, MAKE_CARD(best_min, best_s));
}

/* ======================================================================
 * Strategy helpers
 * ====================================================================== */
static uint8_t _bring_down_ace_king_card(BotLogicC *bl, int suit) {
    int sr = _senior(bl, suit);
    if (_rank_in(sr, bl->suit_ranks[suit], bl->suit_counts[suit])
        && bl->cut_trick_played[suit] == 0)
        return _validate_legal(bl, MAKE_CARD(sr, suit));
    return _validate_legal(bl, MAKE_CARD(_2nd_largest(bl->suit_ranks[suit], bl->suit_counts[suit]), suit));
}

static uint8_t _prepare_for_cut_card(BotLogicC *bl, int suit) {
    int sr = _senior(bl, suit);
    if (_rank_in(sr, bl->suit_ranks[suit], bl->suit_counts[suit]))
        return _validate_legal(bl, MAKE_CARD(sr, suit));
    return _validate_legal(bl, MAKE_CARD(_2nd_largest(bl->suit_ranks[suit], bl->suit_counts[suit]), suit));
}

static uint8_t _compete_spades_card(BotLogicC *bl) {
    int *sp = bl->suit_ranks[SUIT_SPADES];
    int sc = bl->suit_counts[SUIT_SPADES];
    int sr = _senior(bl, SUIT_SPADES);
    int uc = bl->unrevealed_counts[SUIT_SPADES];

    if (sc > 1 && _rank_in(sr, sp, sc) && uc > 1
        && _rank_in(bl->unrevealed[SUIT_SPADES][1], sp, sc))
        return _validate_legal(bl, MAKE_CARD(sr, SUIT_SPADES));
    if (sc > 1 && _rank_in(sr, sp, sc)
        && (uc - sc) <= sc)
        return _validate_legal(bl, MAKE_CARD(sr, SUIT_SPADES));
    int largest = _largest_my(bl, SUIT_SPADES);
    if (_rank_in(10, sp, sc) && largest != 10)
        return _validate_legal(bl, MAKE_CARD(10, SUIT_SPADES));
    if (_rank_in(9, sp, sc) && largest != 9)
        return _validate_legal(bl, MAKE_CARD(9, SUIT_SPADES));
    return _validate_legal(bl, MAKE_CARD(_min_rank(sp, sc), SUIT_SPADES));
}

static uint8_t _get_strategy_card(BotLogicC *bl, PlayStrategy *ps) {
    int suit = ps->suit;
    if (ps->type == STRAT_BRING_DOWN_ACE || ps->type == STRAT_BRING_DOWN_KING)
        return _bring_down_ace_king_card(bl, suit);
    if (ps->type == STRAT_PREPARE_FOR_CUT)
        return _prepare_for_cut_card(bl, suit);
    if (ps->type == STRAT_COMPETE_SPADES)
        return _compete_spades_card(bl);
    return bl->legal[0];
}

/* Remove first COMPETE_SPADES strategy */
static void _remove_compete_strat(BotLogicC *bl) {
    for (int i = 0; i < bl->strategy_count; i++) {
        if (bl->strategies[i].type == STRAT_COMPETE_SPADES
            && bl->strategies[i].suit == SUIT_SPADES) {
            for (int j = i; j < bl->strategy_count - 1; j++)
                bl->strategies[j] = bl->strategies[j+1];
            bl->strategy_count--;
            return;
        }
    }
}

/* ======================================================================
 * Create play strategies (port of _create_play_strategies)
 * ====================================================================== */
static void _create_strategies(BotLogicC *bl) {
    for (int suit = SUIT_DIAMONDS; suit <= SUIT_HEARTS; suit++) {
        if (bl->suit_counts[suit] == 0 || bl->suit_play_turns[suit] != 0) continue;
        int *sc = bl->suit_ranks[suit];
        int sn = bl->suit_counts[suit];
        if (!_has_ace(sc, sn)) {
            if (_has_king(sc, sn)) {
                if (!_has_queen(sc, sn)) {
                    if (sn > 2) {
                        bl->strategies[bl->strategy_count++] =
                            (PlayStrategy){STRAT_BRING_DOWN_ACE, suit};
                    }
                    if (sn == 2) {
                        if (_2nd_largest(sc, sn) >= 7)
                            bl->strategies[bl->strategy_count++] =
                                (PlayStrategy){STRAT_BRING_DOWN_ACE, suit};
                        else
                            bl->strategies[bl->strategy_count++] =
                                (PlayStrategy){STRAT_PREPARE_FOR_CUT, suit};
                    }
                } else { /* has queen */
                    if (sn >= 2)
                        bl->strategies[bl->strategy_count++] =
                            (PlayStrategy){STRAT_BRING_DOWN_KING, suit};
                    else
                        bl->strategies[bl->strategy_count++] =
                            (PlayStrategy){STRAT_PREPARE_FOR_CUT, suit};
                }
            } else if (sn <= 2) {
                bl->strategies[bl->strategy_count++] =
                    (PlayStrategy){STRAT_PREPARE_FOR_CUT, suit};
            }
        }
    }

    /* Spades compete strategies */
    int *sp = bl->suit_ranks[SUIT_SPADES];
    int spn = bl->suit_counts[SUIT_SPADES];
    if (bl->suit_play_turns[SUIT_SPADES] == 0
        && bl->unrevealed_counts[SUIT_SPADES] == 13) {
        if (_rank_in(14, sp, spn) && _rank_in(13, sp, spn)) {
            bl->strategies[bl->strategy_count++] =
                (PlayStrategy){STRAT_COMPETE_SPADES, SUIT_SPADES};
            if (_rank_in(12, sp, spn)) {
                bl->strategies[bl->strategy_count++] =
                    (PlayStrategy){STRAT_COMPETE_SPADES, SUIT_SPADES};
                if (_rank_in(11, sp, spn))
                    bl->strategies[bl->strategy_count++] =
                        (PlayStrategy){STRAT_COMPETE_SPADES, SUIT_SPADES};
            }
            return;
        }
        double spare = spn - bl->total_from_spades;
        if (spare >= bl->spare_spades && bl->bid < 6) {
            int n = (int)(spare - bl->spare_spades + 0.5);
            for (int i = 0; i < n && bl->strategy_count < MAX_STRATEGIES; i++)
                bl->strategies[bl->strategy_count++] =
                    (PlayStrategy){STRAT_COMPETE_SPADES, SUIT_SPADES};
        }
    }
}

/* ======================================================================
 * Play safe card (port of _play_safe_card)
 * ====================================================================== */
static uint8_t _play_safe(BotLogicC *bl, int suit, const uint8_t *played, int pc) {
    int legal_ranks[MAX_LEGAL];
    int lr_count = 0;
    for (int i = 0; i < bl->legal_count; i++)
        legal_ranks[lr_count++] = CARD_RANK(bl->legal[i]);

    if (bl->suit_play_turns[suit] == 1) {
        if (_has_ace(legal_ranks, lr_count)) {
            if (_has_queen(legal_ranks, lr_count) && bl->suit_counts[suit] < 5)
                return _validate_legal(bl, MAKE_CARD(12, suit));
            else
                return _validate_legal(bl, MAKE_CARD(14, suit));
        } else {
            if (_has_king(bl->suit_ranks[suit], bl->suit_counts[suit])
                || _has_queen(bl->suit_ranks[suit], bl->suit_counts[suit])) {
                /* Check if ace not on floor */
                int ace_on_floor = 0;
                for (int i = 0; i < pc; i++)
                    if (played[i] == MAKE_CARD(14, suit)) ace_on_floor = 1;
                if (!ace_on_floor)
                    return _validate_legal(bl, MAKE_CARD(_2nd_largest(legal_ranks, lr_count), suit));
            }
        }
    }

    if (bl->suit_play_turns[suit] >= 2
        && _largest_my(bl, suit) == _senior(bl, suit)
        && bl->cut_trick_played[suit] == 0) {
        uint8_t card = MAKE_CARD(_senior(bl, suit), suit);
        /* Check if it would win */
        uint8_t test[5];
        memcpy(test, played, (size_t)pc);
        test[pc] = card;
        if (card == _winning_card(test, pc + 1))
            return _validate_legal(bl, card);
    }

    return _confuser(bl, _smallest_legal_rank(bl), suit);
}

/* ======================================================================
 * Play spades safely (port of _play_spades_safely)
 * ====================================================================== */
static uint8_t _play_spades_safe(BotLogicC *bl, const uint8_t *played, int pc) {
    int legal_ranks[MAX_LEGAL];
    int lr_count = 0;
    for (int i = 0; i < bl->legal_count; i++)
        legal_ranks[lr_count++] = CARD_RANK(bl->legal[i]);

    int sr = _senior(bl, SUIT_SPADES);
    if (_rank_in(sr, legal_ranks, lr_count)) {
        uint8_t card = MAKE_CARD(sr, SUIT_SPADES);
        uint8_t test[5];
        memcpy(test, played, (size_t)pc);
        test[pc] = card;
        if (card == _winning_card(test, pc + 1))
            return _validate_legal(bl, card);
        else
            return _confuser(bl, _smallest_legal_rank(bl), SUIT_SPADES);
    }

    /* Find best non-consecutive spade */
    int largest = _largest_my(bl, SUIT_SPADES);
    uint8_t card = CARD_NONE;
    for (int i = largest - 1; i > 1; i--) {
        if (_rank_in(i, legal_ranks, lr_count) && _rank_in(i+1, legal_ranks, lr_count))
            continue;
        if (_rank_in(i, legal_ranks, lr_count) && !_rank_in(i+1, legal_ranks, lr_count)) {
            card = MAKE_CARD(i, SUIT_SPADES);
            break;
        }
        card = MAKE_CARD(_2nd_largest(legal_ranks, lr_count), SUIT_SPADES);
    }
    if (card == CARD_NONE)
        card = MAKE_CARD(_2nd_largest(legal_ranks, lr_count), SUIT_SPADES);

    uint8_t test[5];
    memcpy(test, played, (size_t)pc);
    test[pc] = card;
    if (card == _winning_card(test, pc + 1))
        return _validate_legal(bl, card);
    return _confuser(bl, _smallest_legal_rank(bl), SUIT_SPADES);
}

/* ======================================================================
 * Non-spades random card (port of _get_non_spades_random_card)
 * ====================================================================== */
static uint8_t _non_spades_random(BotLogicC *bl, const uint8_t *ns, int ns_c,
                                  int dealer_idx, const int *all_bids) {
    for (int suit = SUIT_DIAMONDS; suit <= SUIT_HEARTS; suit++) {
        int sr = _senior(bl, suit);
        if (_rank_in(sr, bl->suit_ranks[suit], bl->suit_counts[suit])
            && bl->cut_trick_played[suit] == 0) {
            if (bl->suit_play_turns[suit] < 2)
                return MAKE_CARD(sr, suit);
            if (bl->suit_play_turns[suit] == 2 && bl->player_idx == dealer_idx) {
                int total = 0;
                for (int i = 0; i < 4; i++) total += all_bids[i];
                if (total < 8)
                    return MAKE_CARD(sr, suit);
            }
        }
    }
    return _random_card(bl, ns, ns_c);
}

/* ======================================================================
 * First throw turn logic (port of _get_card_from_first_throw_turn_logic)
 * ====================================================================== */
static uint8_t _first_throw(BotLogicC *bl, const uint8_t *ns, int ns_c,
                            int dealer_idx, const int *all_bids) {
    int *sp = bl->suit_ranks[SUIT_SPADES];
    int spn = bl->suit_counts[SUIT_SPADES];
    int sp_unrev = bl->unrevealed_counts[SUIT_SPADES];

    /* No spades remain with opponents */
    if (sp_unrev == spn && spn > 0) {
        if (bl->bid - bl->tricks_won > spn)
            return _validate_legal(bl, MAKE_CARD(_max_rank(sp, spn), SUIT_SPADES));
    }

    /* Only one spade remains with opponents */
    if (sp_unrev - spn == 1 && spn > 0
        && _rank_in(_senior(bl, SUIT_SPADES), sp, spn))
        return _validate_legal(bl, MAKE_CARD(_max_rank(sp, spn), SUIT_SPADES));

    for (int suit = SUIT_DIAMONDS; suit <= SUIT_HEARTS; suit++) {
        if (bl->suit_counts[suit] > 0
            && _rank_in(_senior(bl, suit), bl->suit_ranks[suit], bl->suit_counts[suit])
            && sp_unrev == spn)
            return _validate_legal(bl, MAKE_CARD(_max_rank(bl->suit_ranks[suit], bl->suit_counts[suit]), suit));
        if (bl->suit_counts[suit] > 0
            && bl->suit_play_turns[suit] > 2
            && !_rank_in(_senior(bl, suit), bl->suit_ranks[suit], bl->suit_counts[suit])
            && sp_unrev - spn >= 2)
            return _validate_legal(bl, MAKE_CARD(_min_rank(bl->suit_ranks[suit], bl->suit_counts[suit]), suit));
    }

    /* Already met bid: dump */
    if (bl->tricks_won >= bl->bid) {
        if (spn > 0)
            return _validate_legal(bl, MAKE_CARD(_min_rank(sp, spn), SUIT_SPADES));
        for (int suit = SUIT_DIAMONDS; suit <= SUIT_HEARTS; suit++) {
            if (bl->cut_trick_played[suit] == 1
                && bl->suit_counts[suit] > 0
                && sp_unrev - spn >= 2)
                return _validate_legal(bl, MAKE_CARD(_min_rank(bl->suit_ranks[suit], bl->suit_counts[suit]), suit));
        }
    }

    /* Compete spades: top two unrevealed */
    int sr = _senior(bl, SUIT_SPADES);
    if (spn > 1 && _rank_in(sr, sp, spn) && sp_unrev > 1
        && _rank_in(bl->unrevealed[SUIT_SPADES][1], sp, spn)) {
        uint8_t card = _compete_spades_card(bl);
        _remove_compete_strat(bl);
        return card;
    }

    /* Play strategies */
    if (bl->strategy_count > 0) {
        int ri = _rand_int(bl->strategy_count);
        PlayStrategy ps = bl->strategies[ri];
        int ps_suit = ps.suit;

        if (bl->suit_counts[ps_suit] > 0) {
            uint8_t card;
            if (bl->suit_counts[ps_suit] == 1) {
                if (ps.type == STRAT_PREPARE_FOR_CUT || ps.type == STRAT_COMPETE_SPADES) {
                    card = _get_strategy_card(bl, &ps);
                } else {
                    if (_rank_in(_senior(bl, ps_suit), bl->suit_ranks[ps_suit], bl->suit_counts[ps_suit]))
                        card = _get_strategy_card(bl, &ps);
                    else if (_rank_in(_senior(bl, ps_suit) - 1, bl->suit_ranks[ps_suit], bl->suit_counts[ps_suit])) {
                        if (ns_c > 0)
                            card = _non_spades_random(bl, ns, ns_c, dealer_idx, all_bids);
                        else
                            card = _get_strategy_card(bl, &ps);
                    } else
                        card = _get_strategy_card(bl, &ps);
                }
            } else {
                card = _get_strategy_card(bl, &ps);
            }
            /* Remove used strategy */
            for (int j = ri; j < bl->strategy_count - 1; j++)
                bl->strategies[j] = bl->strategies[j+1];
            bl->strategy_count--;
            return card;
        } else {
            if (ns_c > 0)
                return _non_spades_random(bl, ns, ns_c, dealer_idx, all_bids);
            uint8_t card = _compete_spades_card(bl);
            _remove_compete_strat(bl);
            return card;
        }
    } else {
        for (int suit = SUIT_DIAMONDS; suit <= SUIT_HEARTS; suit++) {
            if (_rank_in(_senior(bl, suit), bl->suit_ranks[suit], bl->suit_counts[suit])
                && bl->suit_play_turns[suit] == 0
                && bl->unrevealed_counts[suit] < 13)
                return _validate_legal(bl, MAKE_CARD(_senior(bl, suit), suit));
        }
    }

    if (ns_c > 0)
        return _non_spades_random(bl, ns, ns_c, dealer_idx, all_bids);

    uint8_t card = _compete_spades_card(bl);
    _remove_compete_strat(bl);
    return card;
}

/* ======================================================================
 * Second throw turn logic
 * ====================================================================== */
static uint8_t _second_throw(BotLogicC *bl, const uint8_t *played, int pc) {
    int first_suit = CARD_SUIT(played[0]);
    if (first_suit != SUIT_SPADES) {
        if (bl->suit_counts[first_suit] > 0)
            return _play_safe(bl, first_suit, played, pc);
        else if (bl->suit_counts[SUIT_SPADES] > 0)
            return _confuser(bl, _smallest_legal_rank(bl), SUIT_SPADES);
        else
            return _cut_no_spades(bl);
    } else {
        if (bl->suit_counts[SUIT_SPADES] > 0)
            return _play_spades_safe(bl, played, pc);
        else
            return _cut_no_spades(bl);
    }
}

/* ======================================================================
 * Third throw turn logic
 * ====================================================================== */
static uint8_t _third_throw(BotLogicC *bl, const uint8_t *played, int pc) {
    int first_suit = CARD_SUIT(played[0]);
    if (first_suit != SUIT_SPADES) {
        if (bl->suit_counts[first_suit] > 0) {
            if (CARD_SUIT(played[1]) != SUIT_SPADES)
                return _play_safe(bl, first_suit, played, pc);
            else
                return _confuser(bl, _smallest_legal_rank(bl), first_suit);
        } else {
            if (bl->suit_counts[SUIT_SPADES] > 0) {
                int max_sp = _max_rank(bl->suit_ranks[SUIT_SPADES], bl->suit_counts[SUIT_SPADES]);
                uint8_t test_card = MAKE_CARD(max_sp, SUIT_SPADES);
                uint8_t test[5];
                memcpy(test, played, (size_t)pc);
                test[pc] = test_card;
                if (test[pc] == _winning_card(test, pc + 1))
                    return _confuser(bl, _smallest_legal_rank(bl), SUIT_SPADES);
                if (_all_spades_legal(bl))
                    return _confuser(bl, _smallest_legal_rank(bl), SUIT_SPADES);
                return _cut_no_spades(bl);
            } else {
                return _cut_no_spades(bl);
            }
        }
    } else {
        if (bl->suit_counts[SUIT_SPADES] > 0)
            return _play_spades_safe(bl, played, pc);
        else
            return _cut_no_spades(bl);
    }
}

/* ======================================================================
 * Last (4th) throw turn logic
 * ====================================================================== */
static uint8_t _last_throw(BotLogicC *bl, const uint8_t *played, int pc) {
    int first_suit = CARD_SUIT(played[0]);

    /* Try to find the cheapest card that wins the trick */
    /* Sort legal cards by rank ascending to find the cheapest winner */
    uint8_t sorted_legal[MAX_LEGAL];
    int sl_count = bl->legal_count;
    memcpy(sorted_legal, bl->legal, (size_t)sl_count);
    /* Simple insertion sort by rank ascending */
    for (int a = 0; a < sl_count - 1; a++) {
        for (int b = a + 1; b < sl_count; b++) {
            /* Compare: spades always rank higher than non-spades for cost */
            int cost_a = (CARD_SUIT(sorted_legal[a]) == SUIT_SPADES && first_suit != SUIT_SPADES)
                         ? (100 + CARD_RANK(sorted_legal[a])) : CARD_RANK(sorted_legal[a]);
            int cost_b = (CARD_SUIT(sorted_legal[b]) == SUIT_SPADES && first_suit != SUIT_SPADES)
                         ? (100 + CARD_RANK(sorted_legal[b])) : CARD_RANK(sorted_legal[b]);
            if (cost_b < cost_a) {
                uint8_t tmp = sorted_legal[a];
                sorted_legal[a] = sorted_legal[b];
                sorted_legal[b] = tmp;
            }
        }
    }

    /* Find the cheapest card that wins */
    for (int i = 0; i < sl_count; i++) {
        uint8_t test[5];
        memcpy(test, played, (size_t)pc);
        test[pc] = sorted_legal[i];
        if (sorted_legal[i] == _winning_card(test, pc + 1))
            return sorted_legal[i];
    }

    /* No card can win — dump cheapest */
    if (bl->suit_counts[first_suit] == 0) {
        if (bl->suit_counts[SUIT_SPADES] == 0)
            return _cut_no_spades(bl);
        else if (_all_spades_legal(bl))
            return _confuser(bl, _smallest_legal_rank(bl), SUIT_SPADES);
        else
            return _cut_no_spades(bl);
    }
    return _confuser(bl, _smallest_legal_rank(bl), first_suit);
}

/* ======================================================================
 * Public: Initialize bot logic
 * ====================================================================== */
void bl_init(BotLogicC *bl, const uint8_t *hand, int hand_size,
             int bid, int tricks_won, int player_idx) {
    memset(bl, 0, sizeof(BotLogicC));
    bl->player_idx = player_idx;
    bl->bid = bid;
    bl->tricks_won = tricks_won;

    /* Copy hand */
    memcpy(bl->hand, hand, (size_t)hand_size);
    bl->hand_size = hand_size;

    /* Init unrevealed: all ranks 14 down to 2 for each suit */
    for (int s = 0; s < NUM_SUITS; s++) {
        for (int i = 0; i < 13; i++)
            bl->unrevealed[s][i] = 14 - i;
        bl->unrevealed_counts[s] = 13;
    }

    /* Separate cards by suit */
    for (int i = 0; i < hand_size; i++) {
        int r = CARD_RANK(hand[i]);
        int s = CARD_SUIT(hand[i]);
        bl->suit_ranks[s][bl->suit_counts[s]++] = r;
    }
    /* Sort each suit's ranks descending */
    for (int s = 0; s < NUM_SUITS; s++) {
        for (int a = 0; a < bl->suit_counts[s] - 1; a++)
            for (int b = a + 1; b < bl->suit_counts[s]; b++)
                if (bl->suit_ranks[s][b] > bl->suit_ranks[s][a]) {
                    int tmp = bl->suit_ranks[s][a];
                    bl->suit_ranks[s][a] = bl->suit_ranks[s][b];
                    bl->suit_ranks[s][b] = tmp;
                }
    }

    /* Pre-compute spare spades and total_from_spades for strategy creation */
    bl->spare_spades = _evaluate_spare_spades(bl);

    double total_cut_score = 0.0;
    total_cut_score += _calculate_cut_score(bl->suit_counts[SUIT_DIAMONDS]);
    total_cut_score += _calculate_cut_score(bl->suit_counts[SUIT_CLUBS]);
    total_cut_score += _calculate_cut_score(bl->suit_counts[SUIT_HEARTS]);

    int spn = bl->suit_counts[SUIT_SPADES];
    double max_cut = (double)spn - bl->spare_spades;
    if (total_cut_score > max_cut) total_cut_score = max_cut;
    if (total_cut_score < 0.0) total_cut_score = 0.0;

    double confirm_score = 0.0;
    double projected_score = 0.0;
    _calculate_spades_score(bl, total_cut_score, &confirm_score, &projected_score);

    if (total_cut_score > (double)spn - projected_score - bl->spare_spades) {
        total_cut_score = (double)spn - projected_score - bl->spare_spades;
        if (total_cut_score < 0.0) total_cut_score = 0.0;
    }

    bl->total_from_spades = total_cut_score + projected_score;
}

/* ======================================================================
 * Public: Select card to play
 * ====================================================================== */
uint8_t bl_select_card(BotLogicC *bl, int throw_turn,
                       const uint8_t *played, int played_count,
                       int dealer_idx, const int *all_bids) {
    if (bl->legal_count == 1) return bl->legal[0];

    /* Build non-spades legal cards */
    uint8_t ns[MAX_LEGAL];
    int ns_c = 0;
    for (int i = 0; i < bl->legal_count; i++)
        if (CARD_SUIT(bl->legal[i]) != SUIT_SPADES)
            ns[ns_c++] = bl->legal[i];

    uint8_t card;
    if (throw_turn == 0)
        card = _first_throw(bl, ns, ns_c, dealer_idx, all_bids);
    else if (throw_turn == 1)
        card = _second_throw(bl, played, played_count);
    else if (throw_turn == 2)
        card = _third_throw(bl, played, played_count);
    else if (throw_turn == 3)
        card = _last_throw(bl, played, played_count);
    else if (ns_c > 0)
        card = _non_spades_random(bl, ns, ns_c, dealer_idx, all_bids);
    else
        card = _random_card(bl, bl->legal, bl->legal_count);

    return card;
}

/* ======================================================================
 * Public: On card selected (update tracking)
 * ====================================================================== */
void bl_on_card_selected(BotLogicC *bl, uint8_t card, int throw_turn, int is_self) {
    int rank = CARD_RANK(card);
    int suit = CARD_SUIT(card);

    /* Remove from own suit cards if self */
    if (is_self) {
        bl->suit_counts[suit] = _remove_rank(
            bl->suit_ranks[suit], bl->suit_counts[suit], rank);
    }

    /* Remove from unrevealed */
    bl->unrevealed_counts[suit] = _remove_rank(
        bl->unrevealed[suit], bl->unrevealed_counts[suit], rank);

    /* Track suit play turns (first card of trick) */
    if (throw_turn == 0)
        bl->suit_play_turns[suit]++;
}

/* ======================================================================
 * Public: On trick completed
 * ====================================================================== */
void bl_on_trick_completed(BotLogicC *bl, const uint8_t *played,
                           int played_count, int starter_idx) {
    (void)played_count;
    if (played_count < 4) return;
    int first_suit = CARD_SUIT(played[0]);
    uint8_t winner = _winning_card(played, played_count);
    int winner_suit = CARD_SUIT(winner);

    if (first_suit != SUIT_SPADES && winner_suit == SUIT_SPADES)
        bl->cut_trick_played[first_suit] = 1;

    /* Remove strategies if bot was not the trick starter */
    if (bl->player_idx != starter_idx) {
        int i = 0;
        while (i < bl->strategy_count) {
            int stype = bl->strategies[i].type;
            int ssuit = bl->strategies[i].suit;
            if ((stype == STRAT_BRING_DOWN_ACE && ssuit == first_suit)
                || (stype == STRAT_BRING_DOWN_KING && ssuit == first_suit)
                || (stype == STRAT_COMPETE_SPADES && ssuit == first_suit)) {
                for (int j = i; j < bl->strategy_count - 1; j++)
                    bl->strategies[j] = bl->strategies[j+1];
                bl->strategy_count--;
            } else {
                i++;
            }
        }
    }
}

/* ======================================================================
 * Public: On throw turn started
 * ====================================================================== */
void bl_on_throw_turn_started(BotLogicC *bl, int play_turn) {
    if (play_turn == 0)
        _create_strategies(bl);
}

/* ======================================================================
 * Legal card computation — delegate to the full implementation in
 * mcts_engine.c (get_legal_cards_c) which enforces all Callbreak rules
 * including "must play higher" and higher-trump requirements.
 * ====================================================================== */

/* ======================================================================
 * Public: Full rollout using bot logic
 * ====================================================================== */
/* New MCTS Rollout Tuning Knobs */
#define ROLLOUT_EPSILON 0.00     /* Pure deterministic rule-based rollout for PIMC */
/* Rollouts intentionally run the round to completion before scoring. */
#define ROOT_REPLAN_MAX_TOTAL_CARDS 20
#define ROOT_REPLAN_DEPTH 1
#define ROOT_REPLAN_BRANCH_SIMS 4
#define ROOT_REPLAN_ENDGAME_BRANCH_SIMS 6
#define ROOT_REPLAN_TIE_EPSILON 1e-9

/* Reward shaping tuned for a short 5-round match.
 * Primary objective: maximize the chance of finishing 1st overall.
 * Existing score-based signals are preserved, but they are now modulated
 * by the remaining match horizon so early rounds stay measured and late
 * rounds become increasingly placement-driven. */
#define REWARD_STANDINGS_BASE_SCALE        6.0
#define REWARD_STANDINGS_FUTURE_SCALE      3.0
#define REWARD_REQUIRED_SWING_SCALE        3.0
#define REWARD_LEAD_BUFFER_SCALE           4.5
#define REWARD_SCORE_MARGIN_WEIGHT         0.42
#define REWARD_ROUND_MARGIN_WEIGHT         0.28
#define REWARD_SELF_ROUND_WEIGHT           0.34
#define REWARD_FIRST_PLACE_WEIGHT          1.25
#define REWARD_PLACEMENT_WEIGHT            0.95
#define REWARD_WINNER_TAKE_WEIGHT          1.45
#define REWARD_LEADER_GAP_WEIGHT           0.55
#define REWARD_FINAL_BID_THREAT_WEIGHT     1.10
#define REWARD_FINAL_SAFE_CONVERT_WEIGHT   0.85
#define REWARD_FINAL_TIE_THREAT_MARGIN     0.25

static double _smooth_pairwise_utility(double my_value, double opp_value, double scale) {
    return tanh((my_value - opp_value) / fmax(1.0, scale));
}

static double _placement_signal_for_rank(int projected_rank) {
    switch (projected_rank) {
        case 1: return 1.0;
        case 2: return 0.12;
        case 3: return -0.45;
        default: return -0.90;
    }
}

static int _projected_rank_for_player(const double *final_scores, int player) {
    int projected_rank = 1;
    for (int opp = 0; opp < NUM_PLAYERS; opp++) {
        if (opp == player) continue;
        if (final_scores[opp] > final_scores[player] + ROOT_REPLAN_TIE_EPSILON) {
            projected_rank++;
        }
    }
    return projected_rank;
}

typedef struct {
    CallbreakState state;
    BotLogicC bots[NUM_PLAYERS];
    int play_turn;
} RolloutCtx;

static void _rollout_ctx_init(RolloutCtx *ctx, const CallbreakState *state) {
    memcpy(&ctx->state, state, sizeof(ctx->state));

    for (int i = 0; i < NUM_PLAYERS; i++) {
        bl_init(&ctx->bots[i], ctx->state.hands[i], ctx->state.hand_sizes[i],
                ctx->state.bids[i], ctx->state.tricks_won[i], i);
    }

    for (int t = 0; t < ctx->state.completed_tricks_count; t++) {
        const TrickRecord *tr = &ctx->state.trick_history[t];
        for (int tt = 0; tt < NUM_PLAYERS; tt++) {
            int player = (tr->starter + tt) % NUM_PLAYERS;
            for (int i = 0; i < NUM_PLAYERS; i++) {
                bl_on_card_selected(&ctx->bots[i], tr->cards[tt], tt, i == player);
            }
        }

        for (int i = 0; i < NUM_PLAYERS; i++) {
            bl_on_trick_completed(&ctx->bots[i], tr->cards, NUM_PLAYERS, tr->starter);
        }
    }

    ctx->play_turn = ctx->state.completed_tricks_count;

    for (int tt = 0; tt < ctx->state.cards_played_count; tt++) {
        int player = (ctx->state.trick_starter + tt) % NUM_PLAYERS;
        for (int i = 0; i < NUM_PLAYERS; i++) {
            bl_on_card_selected(&ctx->bots[i], ctx->state.cards_played[tt], tt, i == player);
        }
    }

    if (ctx->state.total_cards_played < TOTAL_CARDS) {
        for (int i = 0; i < NUM_PLAYERS; i++) {
            bl_on_throw_turn_started(&ctx->bots[i], ctx->play_turn);
        }
    }
}

static int _rollout_ctx_legal_cards(const RolloutCtx *ctx, uint8_t *legal) {
    int cur = ctx->state.current_turn;
    return get_legal_cards_c(
        ctx->state.hands[cur], ctx->state.hand_sizes[cur],
        ctx->state.cards_played, ctx->state.cards_played_count,
        ctx->state.led_suit, legal
    );
}

static uint8_t _rollout_ctx_select_policy_card(
    RolloutCtx *ctx,
    const uint8_t *legal,
    int legal_count
) {
    int cur = ctx->state.current_turn;

    memcpy(ctx->bots[cur].legal, legal, (size_t)legal_count);
    ctx->bots[cur].legal_count = legal_count;

    if (legal_count == 1) {
        return legal[0];
    }

    if (_rand_double() > ROLLOUT_EPSILON) {
        uint8_t card = bl_select_card(&ctx->bots[cur], ctx->state.cards_played_count,
                                      ctx->state.cards_played, ctx->state.cards_played_count,
                                      ctx->state.dealer_index, ctx->state.bids);
        for (int i = 0; i < legal_count; i++) {
            if (legal[i] == card) return card;
        }
    }

    return legal[_rand_int(legal_count)];
}

static void _rollout_ctx_apply_card(RolloutCtx *ctx, uint8_t card) {
    int cur = ctx->state.current_turn;
    int throw_turn = ctx->state.cards_played_count;
    int completed_tricks_before = ctx->state.completed_tricks_count;

    state_play_card_inplace_c(&ctx->state, card);

    for (int i = 0; i < NUM_PLAYERS; i++) {
        bl_on_card_selected(&ctx->bots[i], card, throw_turn, i == cur);
    }

    if (ctx->state.completed_tricks_count > completed_tricks_before) {
        const TrickRecord *tr =
            &ctx->state.trick_history[ctx->state.completed_tricks_count - 1];
        ctx->bots[tr->winner].tricks_won = ctx->state.tricks_won[tr->winner];

        for (int i = 0; i < NUM_PLAYERS; i++) {
            bl_on_trick_completed(&ctx->bots[i], tr->cards, NUM_PLAYERS, tr->starter);
        }

        ctx->play_turn++;
        if (ctx->state.total_cards_played < TOTAL_CARDS) {
            for (int i = 0; i < NUM_PLAYERS; i++) {
                bl_on_throw_turn_started(&ctx->bots[i], ctx->play_turn);
            }
        }
    }
}

static void _rollout_ctx_finish(
    RolloutCtx *ctx,
    int root_player,
    int block_leader,
    const double *cumulative_scores,
    int human_index,
    int current_round,
    int total_rounds,
    double *out_utilities
) {
    (void)human_index;
    while (ctx->state.total_cards_played < TOTAL_CARDS) {
        uint8_t legal[MAX_LEGAL];
        int legal_count = _rollout_ctx_legal_cards(ctx, legal);
        if (legal_count <= 0) break;

        uint8_t card = _rollout_ctx_select_policy_card(ctx, legal, legal_count);
        _rollout_ctx_apply_card(ctx, card);
    }

    compute_state_utilities_c(&ctx->state, root_player, block_leader,
                              cumulative_scores, human_index,
                              current_round, total_rounds, out_utilities);
}

static int _root_replan_branch_sims(const RolloutCtx *ctx) {
    int remaining_cards = TOTAL_CARDS - ctx->state.total_cards_played;
    if (remaining_cards <= 12) return ROOT_REPLAN_ENDGAME_BRANCH_SIMS;
    return ROOT_REPLAN_BRANCH_SIMS;
}

static void _rollout_ctx_search(
    RolloutCtx *ctx,
    int root_player,
    int block_leader,
    const double *cumulative_scores,
    int human_index,
    int current_round,
    int total_rounds,
    int root_replans_remaining,
    double *out_utilities
) {
    if (root_replans_remaining <= 0) {
        _rollout_ctx_finish(ctx, root_player, block_leader,
                            cumulative_scores, human_index,
                            current_round, total_rounds, out_utilities);
        return;
    }

    while (ctx->state.total_cards_played < TOTAL_CARDS) {
        uint8_t legal[MAX_LEGAL];
        int legal_count = _rollout_ctx_legal_cards(ctx, legal);
        if (legal_count <= 0) break;

        int remaining_cards = TOTAL_CARDS - ctx->state.total_cards_played;
        if (root_replans_remaining > 0
            && remaining_cards <= ROOT_REPLAN_MAX_TOTAL_CARDS
            && ctx->state.current_turn == root_player
            && legal_count > 1) {
            int branch_sims = _root_replan_branch_sims(ctx);
            double best_root_value = -1e18;
            uint8_t best_card = CARD_NONE;
            double best_utilities[NUM_PLAYERS];

            for (int li = 0; li < legal_count; li++) {
                double summed_utilities[NUM_PLAYERS] = {0};

                for (int sim = 0; sim < branch_sims; sim++) {
                    RolloutCtx branch = *ctx;
                    double branch_utilities[NUM_PLAYERS];

                    _rollout_ctx_apply_card(&branch, legal[li]);
                    if (root_replans_remaining > 1) {
                        _rollout_ctx_search(
                            &branch,
                            root_player,
                            block_leader,
                            cumulative_scores,
                            human_index,
                            current_round,
                            total_rounds,
                            root_replans_remaining - 1,
                            branch_utilities
                        );
                    } else {
                        _rollout_ctx_finish(&branch, root_player, block_leader,
                                            cumulative_scores, human_index,
                                            current_round, total_rounds,
                                            branch_utilities);
                    }

                    for (int p = 0; p < NUM_PLAYERS; p++) {
                        summed_utilities[p] += branch_utilities[p];
                    }
                }

                for (int p = 0; p < NUM_PLAYERS; p++) {
                    summed_utilities[p] /= (double)branch_sims;
                }

                if (best_card == CARD_NONE
                    || summed_utilities[root_player] > best_root_value + ROOT_REPLAN_TIE_EPSILON
                    || (fabs(summed_utilities[root_player] - best_root_value) <= ROOT_REPLAN_TIE_EPSILON
                        && legal[li] < best_card)) {
                    best_root_value = summed_utilities[root_player];
                    best_card = legal[li];
                    memcpy(best_utilities, summed_utilities, sizeof(best_utilities));
                }
            }

            memcpy(out_utilities, best_utilities, sizeof(best_utilities));
            return;
        }

        uint8_t card = _rollout_ctx_select_policy_card(ctx, legal, legal_count);
        _rollout_ctx_apply_card(ctx, card);
    }

    compute_state_utilities_c(&ctx->state, root_player, block_leader,
                              cumulative_scores, human_index,
                              current_round, total_rounds, out_utilities);
}

void compute_state_utilities_c(const CallbreakState *state, int root_player,
                               int block_leader, const double *cumulative_scores,
                               int human_index, int current_round,
                               int total_rounds, double *out_utilities) {
    (void)human_index;
    double round_scores[NUM_PLAYERS];
    double final_scores[NUM_PLAYERS];
    int normalized_total_rounds = total_rounds > 0 ? total_rounds : 5;
    int normalized_current_round = current_round;
    if (normalized_current_round < 1) normalized_current_round = 1;
    if (normalized_current_round > normalized_total_rounds) {
        normalized_current_round = normalized_total_rounds;
    }
    int future_rounds = normalized_total_rounds - normalized_current_round;
    double urgency = (normalized_total_rounds > 1)
        ? (double)(normalized_current_round - 1) / (double)(normalized_total_rounds - 1)
        : 1.0;
    double late_pressure = 0.5 * urgency + 0.5 * urgency * urgency;
    double pairwise_scale = REWARD_STANDINGS_BASE_SCALE
        + REWARD_STANDINGS_FUTURE_SCALE * (double)future_rounds;

    for (int i = 0; i < NUM_PLAYERS; i++) {
        int p_tricks = state->tricks_won[i];
        int p_bid = state->bids[i];
        round_scores[i] = (p_tricks < p_bid)
            ? -(double)p_bid
            : (double)p_bid + 0.1 * (double)(p_tricks - p_bid);
        final_scores[i] = (cumulative_scores ? cumulative_scores[i] : 0.0) + round_scores[i];
    }

    for (int player = 0; player < NUM_PLAYERS; player++) {
        double my_score = round_scores[player];
        double my_final = final_scores[player];
        double pairwise_utility = 0.0;
        double sum_opp_final = 0.0;
        double sum_opp_round = 0.0;
        double top_opp_final = -1e18;
        double leader_final = 0.0;
        int leader_idx = -1;
        double leader_cumulative = -1e18;

        for (int opp = 0; opp < NUM_PLAYERS; opp++) {
            if (opp == player) continue;
            double o_cumulative = cumulative_scores ? cumulative_scores[opp] : 0.0;
            double o_final = final_scores[opp];
            sum_opp_final += o_final;
            sum_opp_round += round_scores[opp];
            pairwise_utility += _smooth_pairwise_utility(my_final, o_final, pairwise_scale);
            if (o_final > top_opp_final) top_opp_final = o_final;
            if (o_cumulative > leader_cumulative) {
                leader_cumulative = o_cumulative;
                leader_idx = opp;
                leader_final = o_final;
            }
        }

        double avg_opp_final = sum_opp_final / 3.0;
        double avg_opp_round = sum_opp_round / 3.0;
        double score_margin_signal =
            tanh((my_final - avg_opp_final) / (pairwise_scale + 2.0));
        double round_margin_signal =
            tanh((my_score - avg_opp_round) / 4.0);
        double self_round_signal = tanh(my_score / 4.0);
        int projected_rank = _projected_rank_for_player(final_scores, player);
        double placement_signal = _placement_signal_for_rank(projected_rank);
        double winner_take_signal = projected_rank == 1 ? 1.0 : -0.55;
        double first_place_signal = 0.0;

        if (my_final >= top_opp_final - ROOT_REPLAN_TIE_EPSILON) {
            double lead_gap = my_final - top_opp_final;
            first_place_signal = tanh(
                lead_gap / (REWARD_LEAD_BUFFER_SCALE + 1.5 * (double)future_rounds)
            );
        } else {
            int rounds_to_recover = future_rounds > 0 ? future_rounds : 1;
            double required_swing = (top_opp_final - my_final) / (double)rounds_to_recover;
            first_place_signal = -tanh(required_swing / REWARD_REQUIRED_SWING_SCALE);
        }

        double pairwise_weight = 1.35 + 0.75 * late_pressure;
        double first_place_weight = 0.60 + REWARD_FIRST_PLACE_WEIGHT * late_pressure;
        double placement_weight = 0.10 + REWARD_PLACEMENT_WEIGHT * late_pressure;
        double winner_take_weight = 0.05 + REWARD_WINNER_TAKE_WEIGHT * late_pressure;
        double score_margin_weight = 0.18 + REWARD_SCORE_MARGIN_WEIGHT * late_pressure;
        double round_margin_weight = REWARD_ROUND_MARGIN_WEIGHT - 0.08 * late_pressure;
        double self_round_weight = 0.18 + REWARD_SELF_ROUND_WEIGHT * late_pressure;

        double reward =
            pairwise_weight * pairwise_utility
            + first_place_weight * first_place_signal
            + placement_weight * placement_signal
            + winner_take_weight * winner_take_signal
            + score_margin_weight * score_margin_signal
            + round_margin_weight * round_margin_signal
            + self_round_weight * self_round_signal;

        if (player == root_player && block_leader && cumulative_scores && leader_idx >= 0) {
            double leader_weight = 0.18 + REWARD_LEADER_GAP_WEIGHT * late_pressure;
            reward += leader_weight * _smooth_pairwise_utility(my_final, leader_final, pairwise_scale);
        }

        if (player == root_player && cumulative_scores && future_rounds == 0) {
            double my_bid_floor = cumulative_scores[player] + (double)state->bids[player];
            double max_opp_bid_floor = -1e18;
            int my_made_bid = state->tricks_won[player] >= state->bids[player];

            for (int opp = 0; opp < NUM_PLAYERS; opp++) {
                if (opp == player) continue;
                double opp_bid_floor = cumulative_scores[opp] + (double)state->bids[opp];
                if (opp_bid_floor > max_opp_bid_floor) max_opp_bid_floor = opp_bid_floor;

                if (opp_bid_floor >= my_bid_floor - REWARD_FINAL_TIE_THREAT_MARGIN) {
                    double adjusted_gap = opp_bid_floor - my_bid_floor + REWARD_FINAL_TIE_THREAT_MARGIN;
                    double threat_signal = tanh(adjusted_gap / 2.5);
                    int opp_made_bid = state->tricks_won[opp] >= state->bids[opp];
                    reward += REWARD_FINAL_BID_THREAT_WEIGHT
                        * (opp_made_bid ? -threat_signal : 0.75 * threat_signal);
                }
            }

            if (my_bid_floor > max_opp_bid_floor + ROOT_REPLAN_TIE_EPSILON) {
                double safety_signal = tanh((my_bid_floor - max_opp_bid_floor) / 2.5);
                reward += REWARD_FINAL_SAFE_CONVERT_WEIGHT
                    * (my_made_bid ? safety_signal : -(1.15 * safety_signal + 0.20));
            }
        }

        out_utilities[player] = reward;
    }
}

void bot_logic_rollout_vector(CallbreakState *state, int root_player,
                              int block_leader, const double *cumulative_scores,
                              int human_index, int current_round,
                              int total_rounds, double *out_utilities) {
    RolloutCtx ctx;
    _rollout_ctx_init(&ctx, state);
    _rollout_ctx_search(&ctx, root_player, block_leader,
                        cumulative_scores, human_index,
                        current_round, total_rounds,
                        ROOT_REPLAN_DEPTH, out_utilities);
}

double bot_logic_rollout(CallbreakState *state, int root_player,
                         int block_leader, const double *cumulative_scores,
                         int human_index, int current_round,
                         int total_rounds) {
    double utilities[NUM_PLAYERS];
    bot_logic_rollout_vector(state, root_player, block_leader,
                             cumulative_scores, human_index,
                             current_round, total_rounds, utilities);
    return utilities[root_player];
}
