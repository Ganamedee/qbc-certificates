"""What the checker checks: every project, every certificate and the two
milestones it confirms. skool/certificates/README.md section 1 is the source;
this module is that table in a form the checker can run, and the claim forms
are written from it (publish.py).

A project is either a ladder project (01 to 10: the club's own test file runs
against the member's starter/ code) or a pathway build card (FIN, OPT, QML,
RES, SEC, SWE: the card's acceptance checks).
"""

from __future__ import annotations

from dataclasses import dataclass, field

CLUB_PYTHON = "3.13"
# The stack the ladder tests run on: the starter packs' requirements.txt, plus
# pytest and pypdf for the checker itself.
CLUB_REQUIREMENTS = ("qiskit==2.5.2", "qiskit-aer==0.17.2", "matplotlib==3.11.2",
                     "pytest==9.1.1", "pypdf==6.19.0")

# The club's own GitHub accounts: their commits (a starter pack's first commit,
# say) are neither the member's nor someone else's.
CLUB_ACCOUNTS = ("Ganamedee",)
# Where claims are made, and the site certificates live on.
CLAIMS_REPO = "Ganamedee/qbc-certificates"
BASE_URL = "https://quantumbuilders.club"

# Commit history: "several commits, not one upload" (Section 14).
MIN_COMMITS = 3
MIN_CODE_COMMITS = 2

# Build Sprint Graduate, from the sprint repo's pushes (sprint.py): the goal
# pushed in week 0, work pushed in at least 4 of the 6 weeks after it, and
# What I shipped pushed by the end of week 6.
SPRINT_WEEKS = 6
SPRINT_MIN_WEEKS = 4
SPRINT_GOAL_WORDS = 8
SPRINT_SHIPPED_WORDS = 30

# Minutes a step may take on a GitHub runner before it counts as failed.
TEST_MINUTES = 10
BUILD_MINUTES = 20
INSTALL_MINUTES = 10


@dataclass(frozen=True)
class Need:
    """One thing the README must have. kind is one of: charts, run, added,
    limits, portfolio, where, doesnt_show, notice, passed, mention, link."""
    kind: str
    arg: str = ""
    label: str = ""


@dataclass(frozen=True)
class Project:
    key: str
    title: str
    kind: str                       # "ladder" or "card"
    charts: tuple = ()
    readme: tuple = ()              # Need, in the order the report lists them
    # Ladder projects
    folder: str = ""                # projects/<folder>
    module: str = ""                # the member's code, starter/<module>
    tests: str = ""                 # the club's core test file, tests/<name>
    expected: int = 0
    # Build cards
    card: str = ""                  # skool/pathways/<card>
    card_test: str = ""             # first test in the card's test block
    card_test_file: str = ""        # where the member keeps that block
    card_expected: int = 0
    build: str = ""                 # the script the card runs before the tests
    member_tests: tuple = ()        # tests of the member's own that must pass
    min_tests: int = 0              # or: at least this many, all passing
    special: tuple = ()             # extra checks in pathway.py

    @property
    def ladder(self) -> bool:
        return self.kind == "ladder"


LADDER_README = (Need("charts"), Need("run"), Need("added"), Need("limits"), Need("portfolio"))


def _ladder(key, title, folder, module, tests, expected, charts, notice=""):
    readme = LADDER_README + ((Need("notice", notice),) if notice else ())
    return Project(key=key, title=title, kind="ladder", folder=folder, module=module,
                   tests=tests, expected=expected, charts=tuple(charts), readme=readme)


LADDER = (
    _ladder("01", "Project 01: Quantum Coin Flip", "01-quantum-coin-flip",
            "quantum_coin.py", "test_quantum_coin.py", 18, ["coin_results.png"]),
    _ladder("02", "Project 02: Quantum Dice Roller", "02-quantum-dice-roller",
            "quantum_dice.py", "test_quantum_dice.py", 16, ["dice_results.png"]),
    _ladder("03", "Project 03: Quantum Random Number Generator",
            "03-quantum-random-number-generator", "quantum_rng.py", "test_quantum_rng.py",
            18, ["qrng_results.png"]),
    _ladder("04", "Project 04: Quantum Password Generator", "04-quantum-password-generator",
            "quantum_password.py", "test_quantum_password.py", 17, ["pw_results.png"],
            notice="security"),
    _ladder("05", "Project 05: Grover's Algorithm, the Quantum Search Engine",
            "05-quantum-search-engine-demo", "quantum_search.py", "test_quantum_search.py",
            20, ["search_results.png"]),
    _ladder("06", "Project 06: Quantum Maze Solver", "06-quantum-maze-solver",
            "quantum_maze.py", "test_quantum_maze.py", 19, ["maze_results.png"]),
    _ladder("07", "Project 07: Quantum Key Distribution (BB84)",
            "07-quantum-key-distribution-bb84", "quantum_bb84.py", "test_quantum_bb84.py",
            19, ["bb84_results.png"], notice="educational"),
    _ladder("08", "Project 08: Break RSA in Miniature with Shor's Algorithm",
            "08-shors-algorithm", "quantum_shor.py", "test_quantum_shor.py", 26,
            ["shor_results.png"]),
    _ladder("09", "Project 09: Quantum Teleportation and Superdense Coding",
            "09-quantum-teleportation", "quantum_teleport.py", "test_quantum_teleport.py",
            23, ["teleport_results.png", "noise_results.png"]),
    _ladder("10", "Project 10: Quantum Error Correction, Beat the Noise",
            "10-quantum-error-correction", "error_correction.py", "test_error_correction.py",
            23, ["qec_results.png"]),
)

WHERE = Need("where", label='a "Where quantum helps and where it doesn\'t" section')

CARDS = (
    Project(key="swe1", title="SWE-1: pip-installable QRNG package with CI", kind="card",
            card="quantum-software-developer.md",
            readme=(Need("run"),
                    Need("mention", r"pseudo-?random", "says simulator output is pseudo-random")),
            special=("swe1",)),
    Project(key="fin1", title="FIN-1: Classical Monte Carlo option pricer", kind="card",
            card="quantum-finance.md", charts=("convergence.png",),
            readme=(Need("charts"), Need("run"), WHERE, Need("passed", "6")),
            member_tests=("test_black_scholes_reference", "test_put_call_parity",
                          "test_monte_carlo_within_error", "test_grid_probabilities",
                          "test_grid_converges", "test_convergence_slope")),
    Project(key="fin2", title="FIN-2: Amplitude-estimation option pricer", kind="card",
            card="quantum-finance.md", charts=("error_vs_calls.png",),
            readme=(Need("charts"), Need("run"), WHERE, Need("passed", "6"),
                    Need("mention", r"quadratic", 'qualifies the speedup ("quadratic")'),
                    Need("mention", r"classical|monte carlo", "compares with the classical baseline")),
            member_tests=("test_distribution", "test_a_operator_matches_exact_sum",
                          "test_grover_powers", "test_oracle_calls",
                          "test_mle_recovers_from_sampled_circuits", "test_slopes"),
            special=("fin4",)),
    Project(key="qml1", title="QML-1: Variational quantum classifier", kind="card",
            card="quantum-machine-learning.md", charts=("training_curves.png", "accuracy_by_model.png"),
            readme=(Need("charts"), Need("run"), WHERE,
                    Need("mention", r"±|\+/-|\+-|std|standard deviation",
                         "reports mean ± standard deviation")),
            card_test="test_split_is_fixed_and_disjoint", card_test_file="tests/test_qml1.py",
            card_expected=8, build="run_all.py"),
    Project(key="opt1", title="OPT-1: QAOA for MaxCut", kind="card",
            card="quantum-optimisation.md", charts=("landscape_p1.png",),
            readme=(Need("charts"), Need("run"), WHERE, Need("passed", "6"),
                    Need("mention", r"brute.?force", "compares with brute force"),
                    Need("mention", r"approximation ratio|probability",
                         "reports the approximation ratio or success probability")),
            member_tests=("test_cut_value", "test_brute_force", "test_bit_order",
                          "test_local_search_baseline", "test_probabilities_sum_to_one",
                          "test_qaoa_depths")),
    Project(key="opt2", title="OPT-2: QUBO formulation", kind="card",
            card="quantum-optimisation.md", charts=("penalty_sweep.png",),
            readme=(Need("charts"), Need("run"), Need("passed", "6"),
                    Need("mention", r"variable", "writes out the variables"),
                    Need("mention", r"objective", "writes out the objective"),
                    Need("mention", r"penalt", "writes out the penalties")),
            member_tests=("test_qubo_matches_model", "test_ising_matches_qubo",
                          "test_penalty_too_small", "test_brute_force_valid", "test_annealing",
                          "test_qaoa_probabilities")),
    Project(key="sec1", title="SEC-1: Post-quantum cryptography demo", kind="card",
            card="quantum-security.md", charts=("pqc_results.png",),
            readme=(Need("charts"), Need("run"),
                    Need("mention", r"demo,? not production", 'has the "demo, not production cryptography" note'),
                    Need("mention", r"FIPS\s*203", "names ML-KEM's standard, FIPS 203"),
                    Need("mention", r"FIPS\s*204", "names ML-DSA's standard, FIPS 204"),
                    Need("mention", r"ML-KEM-768", "covers ML-KEM-768"),
                    Need("mention", r"ML-DSA-65", "covers ML-DSA-65"),
                    Need("mention", r"X25519", "covers the hybrid X25519 exchange")),
            min_tests=7, special=("sec1",)),
    Project(key="res1", title="RES-1: Bell inequality (CHSH) lab", kind="card",
            card="research-preparation.md", charts=("chsh_vs_noise.png",),
            readme=(Need("charts"), Need("run"),
                    Need("doesnt_show", label='a "what this does and doesn\'t show" part')),
            card_test="test_best_classical_strategy_is_two", card_test_file="tests/test_chsh.py",
            card_expected=4, build="sweep.py"),
    Project(key="res2", title="RES-2: Reproduction of the barren-plateau figure", kind="card",
            card="research-preparation.md", charts=("variance_vs_qubits.png",),
            readme=(Need("charts"), Need("run"),
                    Need("link", "report/report.pdf", "links report/report.pdf")),
            card_test="test_circuit_shape", card_test_file="tests/test_barren.py",
            card_expected=7, build="reproduce.py", special=("res2",)),
)

PROJECTS = {p.key: p for p in LADDER + CARDS}


@dataclass(frozen=True)
class Slot:
    """One repo a claim asks for. project is a PROJECTS key, "any" (any public
    repo of theirs), "ladder" (any of Projects 01 to 10, found from its code),
    "sprint" (a Build Sprint repo, checked by sprint.py) or "fin4" (the FIN-4
    memo)."""
    key: str
    label: str
    project: str
    optional: bool = False
    note: str = ""


@dataclass(frozen=True)
class Claim:
    id: str
    title: str
    kind: str                       # "certificate" or "milestone"
    slots: tuple
    intro: str = ""
    pinned_requirements: bool = False   # SWE: 01 to 03 with a pinned requirements.txt
    history: bool = True            # check commit history
    readme: bool = True             # check READMEs, charts and the rest of the project
    form_order: int = 0
    extra: dict = field(default_factory=dict)


def _ladder_slots(*keys):
    return tuple(Slot(k, PROJECTS[k].title, k) for k in keys)


def _card_slot(key, note=""):
    return Slot(key, PROJECTS[key].title, key, note=note)


SPRINT_NOTE = ("Your sprint is checked from your sprint repo's push history on GitHub, which records "
               "when each push arrived: when you pushed your goal, the weeks you pushed work, and "
               "your What I shipped section.")

CLAIMS = (
    Claim("club_foundations", "Quantum Builder: Foundations", "certificate",
          _ladder_slots("01", "02", "03", "04"), form_order=1),
    Claim("club_intermediate", "Quantum Builder: Intermediate", "certificate",
          _ladder_slots("05", "06", "07"), form_order=2),
    Claim("club_advanced", "Quantum Builder: Advanced", "certificate",
          _ladder_slots("08", "09", "10"), form_order=3),
    Claim("club_path_software", "Quantum Software Developer Pathway", "certificate",
          _ladder_slots("01", "02", "03") + (_card_slot("swe1"),),
          pinned_requirements=True, form_order=4),
    Claim("club_path_finance", "Quantum Finance Pathway", "certificate",
          _ladder_slots("01", "02", "03")
          + (_card_slot("fin1"), _card_slot("fin2"),
             Slot("fin4", "FIN-4 memo (only if it isn't in your FIN-2 repo)", "fin4",
                  optional=True, note="Leave it empty when memo.md or memo.pdf is in your FIN-2 repo.")),
          form_order=5),
    Claim("club_path_qml", "Quantum Machine Learning Pathway", "certificate",
          _ladder_slots("01", "02", "03") + (_card_slot("qml1"),), form_order=6),
    Claim("club_path_optimisation", "Quantum Optimisation Pathway", "certificate",
          _ladder_slots("01", "02", "03") + (_card_slot("opt1"), _card_slot("opt2")), form_order=7),
    Claim("club_path_security", "Quantum Security Pathway", "certificate",
          _ladder_slots("01", "02", "03", "04", "07") + (_card_slot("sec1"),), form_order=8),
    Claim("club_path_research", "Research Preparation Pathway", "certificate",
          _ladder_slots("01", "02", "03") + (_card_slot("res1"), _card_slot("res2")),
          form_order=9),
    Claim("club_sprint", "Build Sprint Graduate", "certificate",
          (Slot("sprint", "Your Build Sprint project", "sprint"),),
          history=False, readme=False, intro=SPRINT_NOTE, form_order=10),
    Claim("milestone_2", "Milestone 2: First Repo", "milestone",
          (Slot("repo1", "Your first public repo", "any"),),
          history=False, readme=False, form_order=11),
    Claim("milestone_3", "Milestone 3: 3-Project Portfolio", "milestone",
          (Slot("repo1", "Project repo 1", "ladder"), Slot("repo2", "Project repo 2", "ladder"),
           Slot("repo3", "Project repo 3", "ladder")),
          history=False, readme=False, form_order=12),
)

CLAIMS_BY_ID = {c.id: c for c in CLAIMS}
CLAIMS_BY_TITLE = {c.title.lower(): c for c in CLAIMS}
MODULES = {p.module: p for p in LADDER}


def claim(claim_id: str) -> Claim:
    return CLAIMS_BY_ID[claim_id]
