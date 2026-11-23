import pytest

from adaptation_ai import AdaptationAI
from behavior_analyzer import BehaviorAnalyzer


class TestBehaviorAnalyzer:
    def test_discovery_counts_as_exploration(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("discover")
        assert ba.profile["exploration"] == 1
        assert ba.get_action_ratios()["explore"] == 1

    def test_update_profile_move(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("move")
        assert ba.profile["exploration"] == 1

    def test_update_profile_take(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("take")
        assert ba.profile["strategy"] == 1

    def test_update_profile_use(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("use")
        assert ba.profile["strategy"] == 1

    def test_update_profile_attack(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("attack")
        assert ba.profile["aggression"] == 1

    def test_get_action_ratios(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("move")
        ba.update_profile("move")
        ba.update_profile("attack")
        ba.update_profile("use")
        ratios = ba.get_action_ratios()
        assert ratios["explore"] == pytest.approx(0.5)
        assert ratios["attack"] == pytest.approx(0.25)
        assert ratios["use"] == pytest.approx(0.25)

    def test_get_action_ratios_empty(self):
        ba = BehaviorAnalyzer()
        ratios = ba.get_action_ratios()
        assert ratios == {"explore": 0.0, "attack": 0.0, "use": 0.0}

    def test_get_profile_summary_explorer(self):
        ba = BehaviorAnalyzer()
        for _ in range(6):
            ba.update_profile("move")
        ba.update_profile("take")
        summary = ba.get_profile_summary()
        assert "explorer" in summary.lower()

    def test_get_profile_summary_aggressor(self):
        ba = BehaviorAnalyzer()
        for _ in range(5):
            ba.update_profile("attack")
        ba.update_profile("move")
        summary = ba.get_profile_summary()
        assert "aggress" in summary.lower() or "fighter" in summary.lower()

    def test_get_profile_summary_strategist(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("take")
        ba.update_profile("use")
        ba.mark_book_read()
        ba.update_profile("attack")
        summary = ba.get_profile_summary()
        assert "strategist" in summary.lower() or "prepares" in summary.lower()

    def test_mark_book_read(self):
        ba = BehaviorAnalyzer()
        assert ba.read_book is False
        ba.mark_book_read()
        assert ba.read_book is True

    def test_read_book_before_attack_true(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("use")
        ba.mark_book_read()
        ba.update_profile("attack")
        assert ba.read_book_before_attack() is True

    def test_read_book_before_attack_false(self):
        ba = BehaviorAnalyzer()
        ba.update_profile("attack")
        ba.update_profile("use")
        assert ba.read_book_before_attack() is False


class TestAdaptationAI:
    def test_adjust_difficulty_increases_on_aggression(self):
        ai = AdaptationAI()
        profile = {"exploration": 0, "aggression": 6, "strategy": 0}
        ai.adjust_difficulty(profile)
        assert ai.get_difficulty() == 1.5

    def test_adjust_difficulty_decreases_on_exploration(self):
        ai = AdaptationAI()
        profile = {"exploration": 0, "aggression": 0, "strategy": 6}
        ai.adjust_difficulty(profile)
        assert ai.get_difficulty() == 0.8

    def test_get_difficulty_default(self):
        ai = AdaptationAI()
        assert ai.get_difficulty() == 1.0

    def test_adapt_world_blocks_corridor_for_aggressor(self, wmap):
        ai = AdaptationAI()
        profile = {"exploration": 0, "aggression": 4, "strategy": 0}
        ai.adapt_world(wmap, profile, "aggressive fighter")
        assert "north" not in wmap["Corridor"].exits
        assert ai._corridor_blocked is True

    def test_adapt_world_adds_hidden_passage_for_explorer(self, wmap):
        ai = AdaptationAI()
        profile = {"exploration": 5, "aggression": 0, "strategy": 0}
        ai.adapt_world(wmap, profile, "cautious explorer")
        assert wmap["Library"].exits.get("west") == "Hidden Study"
        assert wmap["Hidden Study"].exits.get("east") == "Library"
        assert ai._hidden_passage_revealed is True
