# Dual-Stream Joint Vision Pipeline: Research & Roadmap
## Pair-First Image Processing, Cross-Validation & Multi-View Consensus

---

## 1. Executive Summary: The Paradigm Shift

Traditionally, Automated License Plate Recognition (ALPR) systems treat every incoming image in complete isolation:

```
[ Traditional Isolated Pipeline ]
Image A ──> YOLO Detect ──> Crop ──> OCR ──> Normalize ──> Plate A
Image B ──> YOLO Detect ──> Crop ──> OCR ──> Normalize ──> Plate B
                                                               │
                                                               ▼
                                                  [ Matcher: Plate A == Plate B? ]
```

### The Problem With the Isolated Approach
- If **Image A** has glare, dirt, shadow, or motion blur, OCR might misread `UAR 123X` as `UA8 123X` or fail detection entirely.
- Even though **Image B** is crystal clear (`UAR 123X`, 97% confidence), the system marks the pair as **CONFLICT** or **NEEDS_REVIEW**.
- The isolated pipeline cannot share context: Image A doesn't know Image B exists, and Image B doesn't know Image A exists until both have already finished and potentially failed.

### The Proposed Paradigm: Pair-First Joint Vision
Now that our **Smart Multi-Signal Pair Association** (chronological sorting, camera naming sequences, serpentine bike traversal, and interactive linker) can propose or establish pair candidates upfront, we can flip the pipeline:

```
                          [ PROPOSED CANDIDATE PAIR ]
                            (Image A  &  Image B)
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │   DUAL-STREAM VISION ENGINE   │
                      └───────────────┬───────────────┘
                                      │
             ┌────────────────────────┴────────────────────────┐
             ▼                                                 ▼
    [ Stream 1: Image A ]                             [ Stream 2: Image B ]
    - YOLO Localize                                   - YOLO Localize
    - Preprocess & CLAHE                              - Preprocess & CLAHE
    - Raw OCR Extraction                              - Raw OCR Extraction
    - Color Distribution (White/Yellow)               - Color Distribution (White/Yellow)
             │                                                 │
             └────────────────────────┬────────────────────────┘
                                      │
                                      ▼
                      ┌───────────────────────────────┐
                      │  JOINT RECONCILIATION LAYER   │
                      └───────────────┬───────────────┘
                                      │
       ├── 1. Differential Orientation Consensus (Yellow = Rear, White = Front)
       ├── 2. Character-Level Cross-Validation & Syntax Constraint Voting
       ├── 3. Asymmetric Recovery (Clear Image guides Noisy Image Re-scan)
       └── 4. Installation Order Bayesian Prior Verification
                                      │
                                      ▼
                      [ VERIFIED INSTALLATION PAIR ]
                   (Instant Auto-Approval / ITMS Ready)
```

---

## 2. Core Scientific & Technical Capabilities

### 2.1 Multi-View Character Consensus & Voting
Instead of comparing two static strings after the fact, the joint pipeline evaluates the character probability distributions of both crops simultaneously:

$$P(\text{Plate} \mid I_{\text{front}}, I_{\text{rear}}) \propto P(I_{\text{front}} \mid \text{Plate}) \cdot P(I_{\text{rear}} \mid \text{Plate}) \cdot P(\text{Plate})$$

1. **Exact Agreement ($D_{\text{Levenshtein}} = 0$)**:
   - Both crops yield identical canonical plates (e.g. `UAR 123X`).
   - Confidence is boosted to near 100%. The pair immediately bypasses manual review.

2. **Single-Character Disagreement ($D_{\text{Levenshtein}} = 1$)**:
   - Example: Front yields `UA8 123X` (conf: 0.62), Rear yields `UAB 123X` (conf: 0.91).
   - **Syntax Rule Enforcement**: Uganda standard plate format is `UA[A-Z] [0-9]{3}[A-Z]`.
     - Character 3 is structurally required to be an alphabetical letter ($[A-Z]$).
     - `8` is syntactically invalid in slot 3; `B` is valid!
     - The joint engine resolves the character without human intervention.
   - **Ambiguity Confusion Matrix**:
     - `8 ↔ B`, `0 ↔ O`, `1 ↔ I`, `5 ↔ S`, `Z ↔ 7`.
     - When two crops disagree on a known confusion pair, the higher-contrast stroke feature or syntax rule automatically breaks the tie.

3. **Asymmetric Visibility & Guided Hypothesis Testing**:
   - Scenario: The rear plate is clean (`UBF 452K`), but the front plate is dusty or in heavy shadow, causing isolated YOLO or OCR to return no text.
   - **Joint Solution**: The clear rear plate provides a concrete **prior hypothesis**: `"Does the front crop contain UBF 452K?"`
   - Rather than generic blind OCR, the engine runs a targeted template cross-correlation / multi-threshold binarization specifically validating the stroke positions of `UBF 452K`. If feature match exceeds threshold, the pair is confirmed.

---

### 2.2 Differential Color Analysis for 100% Orientation Accuracy
In Uganda:
- **Commercial/Private Vehicles**: Front plates are **white reflective background** with black text; Rear plates are **yellow reflective background** with black text.
- **Motorcycles**: Rear plates are yellow/white square plates; front fenders often carry smaller white stickers or narrow plates.

In an isolated pipeline, single-image lighting (sunset golden hour, neon lights, sodium vapor street lamps) can trick an orientation classifier into thinking a white plate is yellow.
In a **Dual-Stream Pipeline**, the two images were captured at virtually the same time under identical ambient lighting!
- We perform **differential chromaticity**:
  $$\Delta \text{Yellow} = \text{YellowIndex}(I_B) - \text{YellowIndex}(I_A)$$
- Whichever image exhibits higher yellow/amber saturation relative to its partner is definitively the **REAR**, while the more luminance-dominated neutral partner is definitively the **FRONT**.
- Orientation misclassifications drop to near zero.

---

### 2.3 Installation Order Prior (Database Triangulation)
The joint pipeline has direct access to the database of open `InstallationOrder`s:
- If Front OCR yields `UAR 123X` and Rear OCR yields `UAR 128X`:
- The engine queries the database:
  - Is `UAR 123X` an active work order scheduled for this installation center today? **YES.**
  - Is `UAR 128X` an active work order? **NO.**
- The order database serves as a strong Bayesian prior, resolving ambiguities with extreme mathematical confidence.

---

## 3. Potential Edge Cases & Architectural Safeguards

| Edge Case | Risk | Joint Safeguard |
| :--- | :--- | :--- |
| **False Pair Proposed** | Two entirely different motorbikes paired by sequence error. | If $D_{\text{Levenshtein}} > 3$ and visual features diverge, the engine flags a **PAIRING_MISMATCH** and returns both to the single queue. |
| **Severely Missing Plate** | Vehicle has lost its front plate (only rear installed). | Rear plate is recognized; front is flagged as `MISSING_PHYSICAL_PLATE` (distinct from OCR failure), creating an audit issue. |
| **Adjacent Vehicle Bleed** | Background contains a bumper of another parked bike. | YOLO detection prioritized by bounding box centering and vehicle mask alignment. |
| **Dual Dirt/Degradation** | Both plates partially obscured. | Multi-kernel fusion: combining high-contrast characters from Front with clear characters from Rear. |

---

## 4. Implementation Roadmap

### Phase 1: Dual-Stream Engine Core (`core/vision/joint_pipeline.py`)
- Define `DualStreamResult` dataclass:
  - `front_image`, `rear_image`
  - `consensus_plate`
  - `front_raw_plate`, `rear_raw_plate`
  - `consensus_confidence`
  - `reconciliation_method` (`EXACT_MATCH`, `SYNTAX_RESOLVED`, `ORDER_PRIOR_MATCH`, `MANUAL_DISPUTE`)
  - `orientation_confidence`
- Implement `process_pair(pair: VehicleInstallationPair) -> DualStreamResult`.

### Phase 2: Differential Orientation & Color Fusion
- Implement relative color comparison in HSV / CIELAB color space between the two crops.
- Automatically assign / verify `front_image` and `rear_image` slots on the `VehicleInstallationPair`.

### Phase 3: Character Confusion & Uganda Syntax Arbiter
- Build character-level Levenshtein alignment (`align_plate_strings(p1, p2)`).
- Implement Uganda syntax slot validator (`UA[A-Z] [0-9]{3}[A-Z]`).
- Confusion pair resolver for `8 ↔ B`, `0 ↔ O`, `1 ↔ I`, `5 ↔ S`, `Z ↔ 7`.

### Phase 4: Guided Re-Scan & Hypothesis Verification
- If one image fails initial OCR or has low confidence ($\le 0.55$), use partner's plate string as a search template.
- Test adaptive CLAHE, Otsu binarization, and morphological dilation targeting partner characters.

### Phase 5: TUI Dashboard & Workflow Integration
- In the TUI Review Queue:
  - Display joint consensus badge: `[green]Consensus: UAR 123X (Front=0.88 ↔ Rear=0.96)[/green]`.
  - Add hotkey `[J]` ("Joint Re-Scan") to re-evaluate active pair with dual-stream vision.
- In `core/management/commands/process_vision.py`:
  - Add `--joint-pairs` mode to process proposed pairs in dual-stream batches.
