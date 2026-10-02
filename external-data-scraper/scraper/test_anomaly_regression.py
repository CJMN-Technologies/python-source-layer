"""
Regression Verification Suite for October 02 Scraped Event Anomalies
Tests dual-layer hardening across:
1. Micro-arterials, bridge underpasses, flyovers, & barangay fiesta concerts (external_lgu_0353 archetype)
2. Civil service & internal government employee work suspensions (external_lgu_0327 archetype)
3. Routine municipal maintenance & public service caravans (0310-0326 archetypes)
4. Preservation of genuine corridor arena events & student class suspensions
"""

import sys
import os

# Fix Windows console encoding
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from keywords import classify_post, _match_keyword
from pipeline import is_micro_venue_or_administrative, is_off_corridor_venue, is_civil_service_work_suspension

def run_tests():
    print("=" * 70)
    print("RUNNING OCT 02 ANOMALY REGRESSION & HARDENING VERIFICATION")
    print("=" * 70)

    total_tests = 0
    passed_tests = 0

    def assert_true(condition, test_name):
        nonlocal total_tests, passed_tests
        total_tests += 1
        if condition:
            passed_tests += 1
            print(f"  [PASS] {test_name}")
        else:
            print(f"  [FAIL] {test_name}")

    # -------------------------------------------------------------------------
    # TEST 1: external_lgu_0353 Archetype (Bridge Underpass / Fiesta Concert)
    # -------------------------------------------------------------------------
    print("\n--- Test Suite 1: Bridge Underpasses / Araw ng Barangay / Fiesta Concerts ---")
    rosario_post = """
    ABISO SA MGA MOTORISTA
    Pansamantalang pagpapasara ng ilalim ng Rosario Bridge kaugnay ng selebrasyon ng Araw ng Barangay Rosario
    Mula October 3, 2026 ganap na 6:00 PM hanggang 12:00 MN
    Gaganapin ang mga aktibidad at live band concert para sa kapistahan.
    Mag-ingat at kumuha ng alternatibong ruta.
    """
    
    cat1 = classify_post(rosario_post)
    assert_true(cat1 is None, "classify_post() drops Rosario Bridge underpass fiesta concert")
    assert_true(is_micro_venue_or_administrative(rosario_post), "is_micro_venue_or_administrative() flags bridge underpass & fiesta")
    assert_true(is_off_corridor_venue(rosario_post), "is_off_corridor_venue() flags Rosario Bridge as off-corridor")

    # Generalized bridge/flyover/fiesta test across other locations
    flyover_post = """
    Traffic Advisory mula sa LGU: Pansamantalang isasara ang C5 Flyover at ilalim ng tulay
    para sa SK Battle of the Bands at fiesta celebration ng barangay.
    """
    cat1_gen = classify_post(flyover_post)
    assert_true(cat1_gen is None, "classify_post() drops generalized C5 flyover / SK concert post")
    assert_true(is_micro_venue_or_administrative(flyover_post), "is_micro_venue_or_administrative() flags flyover & SK concert")

    # -------------------------------------------------------------------------
    # TEST 2: external_lgu_0327 Archetype (Civil Service / Family Week Work Suspension)
    # -------------------------------------------------------------------------
    print("\n--- Test Suite 2: Civil Service / Government Internal Work Suspensions ---")
    family_week_post = """
    MALACAÑANG MEMORANDUM CIRCULAR NO. 64
    SUSPENSION OF WORK IN GOVERNMENT OFFICES IN THE EXECUTIVE BRANCH
    Pursuant to Proclamation No. 60 (s. 1992) declaring the last week of September as National Family Week,
    work in government offices is hereby suspended from 3:00 in the afternoon on Monday, 28 September 2026.
    Those agencies whose functions involve delivery of basic and health services shall continue their operations.
    """
    cat2 = classify_post(family_week_post)
    assert_true(cat2 is None, "classify_post() drops Malacañang MC 64 Family Week government work suspension")
    assert_true(is_civil_service_work_suspension(family_week_post), "is_civil_service_work_suspension() flags government-only suspension")

    city_hall_post = """
    Pansamantalang sinususpinde ang trabaho ng mga kawani sa City Hall at pamahalaang lungsod
    simula 1:00 PM upang bigyang-daan ang General Assembly ng mga kawani. Skeletal workforce ang itatalaga.
    """
    cat2_gen = classify_post(city_hall_post)
    assert_true(cat2_gen is None, "classify_post() drops generalized City Hall internal employee work suspension")
    assert_true(is_civil_service_work_suspension(city_hall_post), "is_civil_service_work_suspension() flags City Hall skeleton workforce")

    # -------------------------------------------------------------------------
    # TEST 3: Routine Civic Maintenance Archetypes (0310-0326)
    # -------------------------------------------------------------------------
    print("\n--- Test Suite 3: Routine Municipal Maintenance & Public Services ---")
    declog_post = "Isinagawa ng City Engineering Department ang declogging ng drainage at paglilinis ng kanal sa Barangay 1."
    assert_true(classify_post(declog_post) is None, "classify_post() drops drainage declogging")

    asphalt_post = "Abiso sa motorista: May asphalt scraping at asphalting operation sa kalye mula 10PM hanggang 4AM."
    assert_true(classify_post(asphalt_post) is None, "classify_post() drops localized road asphalting scraping")

    tupad_post = "Gaganapin ang TUPAD payout at profiling ng DOLE at pamahalaang lungsod sa covered court bukas."
    assert_true(classify_post(tupad_post) is None, "classify_post() drops TUPAD profiling/payout")

    grass_post = "Nagsagawa ng clearing of grass, tree trimming, at pruning ng mga puno ang Parks and Recreation Division."
    assert_true(classify_post(grass_post) is None, "classify_post() drops grass cutting and tree pruning")

    # -------------------------------------------------------------------------
    # TEST 4: Preservation of Genuine Corridor Arena Events
    # -------------------------------------------------------------------------
    print("\n--- Test Suite 4: Preservation of Genuine Corridor Arena Events ---")
    araneta_post = """
    UAAP Season 89 Men's Basketball Finals: UP Fighting Maroons vs DLSU Green Archers
    at the Smart Araneta Coliseum, Cubao. Tip-off at 4:00 PM!
    """
    cat4_araneta = classify_post(araneta_post)
    assert_true(cat4_araneta == "lgu", "classify_post() preserves Smart Araneta Coliseum major sports event")
    assert_true(not is_micro_venue_or_administrative(araneta_post), "is_micro_venue_or_administrative() does not flag Smart Araneta")
    assert_true(not is_off_corridor_venue(araneta_post), "is_off_corridor_venue() correctly recognizes Cubao / Araneta as on-corridor")

    filoil_post = """
    NCAA Men's Basketball at Filoil EcoOil Centre, San Juan Arena. Game starts at 2:00 PM.
    """
    cat4_filoil = classify_post(filoil_post)
    assert_true(cat4_filoil == "lgu", "classify_post() preserves Filoil EcoOil arena event")

    # -------------------------------------------------------------------------
    # TEST 5: Preservation of Genuine Student Class Suspensions
    # -------------------------------------------------------------------------
    print("\n--- Test Suite 5: Preservation of Genuine Student Class Suspensions ---")
    typhoon_post = """
    WALANG PASOK: Dahil sa Tropical Cyclone Signal No. 2 at malalakas na ulan ng Bagyo,
    suspendido ang klase sa lahat ng antas, pampubliko at pribadong paaralan bukas sa buong Lungsod ng Maynila.
    """
    cat5_typhoon = classify_post(typhoon_post)
    assert_true(cat5_typhoon in ("academic", "lgu"), "classify_post() preserves city-wide typhoon class suspension")
    assert_true(not is_civil_service_work_suspension(typhoon_post), "is_civil_service_work_suspension() does not flag typhoon class suspension")

    strike_post = """
    ADVISORY: Dahil sa nakatakdang 2-day nationwide transport strike ng PISTON at MANIBELA,
    ang University of Santo Tomas ay magpapatupad ng shift to Enriched Virtual Mode (EVM) online classes.
    """
    cat5_strike = classify_post(strike_post, source_type="academic")
    assert_true(cat5_strike == "academic", "classify_post() preserves transport strike EVM online shift")

    print("\n" + "=" * 70)
    print(f"VERIFICATION SUMMARY: {passed_tests} / {total_tests} tests PASSED ({passed_tests/total_tests*100:.1f}%)")
    print("=" * 70)
    if passed_tests == total_tests:
        print("ALL TESTS PASSED MATHEMATICALLY. ANOMALY PATTERNS PERMANENTLY BLOCKED.")
    else:
        print("SOME TESTS FAILED! REVIEW FAILURES ABOVE.")
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
