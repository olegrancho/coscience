from coscience import pause


def test_a_fresh_substrate_is_not_paused(tmp_path):
    assert pause.is_paused(tmp_path) is False


def test_set_paused_round_trips(tmp_path):
    pause.set_paused(tmp_path, True)
    assert pause.is_paused(tmp_path) is True
    pause.set_paused(tmp_path, False)
    assert pause.is_paused(tmp_path) is False


def test_pausing_twice_is_harmless(tmp_path):
    pause.set_paused(tmp_path, True)
    pause.set_paused(tmp_path, True)
    assert pause.is_paused(tmp_path) is True


def test_resuming_a_running_platform_is_harmless(tmp_path):
    # The marker was never created; removing it must not raise.
    pause.set_paused(tmp_path, False)
    assert pause.is_paused(tmp_path) is False
