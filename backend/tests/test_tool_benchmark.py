from scripts.benchmark_tools import benchmark


async def test_paired_fixture_measures_cache_without_changing_itinerary():
    report = await benchmark(repetitions=1, delay=0)
    assert report["identical_itinerary_and_validation"]
    control = report["arms"]["serial_no_shared_cache"]["samples"][0]
    current = report["arms"]["current_runtime"]["samples"][0]
    assert control["fixture_tool_calls"] == 8
    assert current["fixture_tool_calls"] == 4
    assert current["cache_hits"] == 4
    assert control["scripted_model_calls"] == current["scripted_model_calls"] == 4
    assert control["quality_hash"] == current["quality_hash"]
