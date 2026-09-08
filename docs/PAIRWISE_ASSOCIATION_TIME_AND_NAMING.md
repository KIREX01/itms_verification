# Pairwise Evidence Association: Time Proximity & Camera Naming Architecture

This document specifies the technical design, mathematical formulations, smartphone naming standards, and architectural implementation for associating front and rear motorcycle evidence photos into complete verification pairs.

---

## 1. The Operational Problem: Motorcycle Fleet Inspection

In motorcycle (boda-boda) fleet verification in Uganda:
- **Rear Plates**: Stamped metal plates with standard black typography on yellow reflective background (e.g., `UMA054ND`), yielding high OCR detection confidence (>85-95%).
- **Front Plates**: Typically stenciled with white or black paint onto curved, mud-spattered front mudguards, or absent entirely. OCR recognition on front mudguards is often partial, low-confidence, or fails due to geometric distortion.
- **The File Naming Problem**: Technicians cannot spend field time manually renaming hundreds of camera roll photos (`IMG_0041.JPG`, `20260908_132250.jpg`). Forcing file renaming causes bottlenecks and human error.
- **Folder Segregation**: Ingestion permits technicians to dump camera rolls into `front/` and `rear/` parent folders without renaming a single photo.

---

## 2. Field Traversal Geometry: The "U-Turn / Snake" Walk

When motorbikes are parked side-by-side in a row of $N$ bikes, a technician cannot walk between individual bikes. The technician walks down the row once and returns on the other side:

```
[Start Walk: Rears] -----------------------------------------------------> [Turnaround]
    Bike 1 (Rear)  →  Bike 2 (Rear)  →  ...  →  Bike N-1 (Rear)  →  Bike N (Rear)
                                                                          │ (turnaround gap)
    Bike 1 (Front) ←  Bike 2 (Front) ←  ...  ←  Bike N-1 (Front) ←  Bike N (Front)
[Finish Walk] <-----------------------------------------------------------┘
```

### Walk Direction Identification Algorithm
The system automatically determines whether the technician performed a **U-Turn (Reverse Walk)** or a **Parallel Walk** by comparing timestamp intervals:

1. Let rear timestamps be $R = [t_{R_1}, t_{R_2}, \dots, t_{R_N}]$ sorted chronologically.
2. Let front timestamps be $F = [t_{F_1}, t_{F_2}, \dots, t_{F_M}]$ sorted chronologically.
3. Compute the turnaround interval $\Delta t_{\text{turnaround}}$ and parallel interval $\Delta t_{\text{parallel}}$:
   $$\Delta t_{\text{turnaround}} = \min(|t_{F_1} - t_{R_N}|, |t_{R_1} - t_{F_M}|)$$
   $$\Delta t_{\text{parallel}} = |t_{F_1} - t_{R_1}|$$
4. **Classification Rule**:
   $$\text{Geometry} = \begin{cases} \text{U-Turn (Reverse Return Pass)}, & \text{if } \Delta t_{\text{turnaround}} \le \Delta t_{\text{parallel}} \\ \text{Parallel Walk (Both Passes Forward)}, & \text{otherwise} \end{cases}$$
5. **Alignment**: If U-turn, whichever pass started second is reversed:
   $$\text{Aligned Fronts} = \text{reversed}(F) \quad \text{if } t_{F_1} \ge t_{R_1} \text{ else } F$$
   $$\text{Aligned Rears} = R \quad \text{if } t_{F_1} \ge t_{R_1} \text{ else } \text{reversed}(R)$$
6. **Pair Matching**:
   $$\text{Pair}_i = (\text{Aligned Fronts}_i, \text{Aligned Rears}_i) \quad \text{for } i \in [0, \min(N, M) - 1]$$

### Session Clustering
If a single batch contains photos taken across multiple sessions (e.g. morning vs. afternoon, or different days), photos are partitioned into sessions:
$$\text{Split Session if } |t_{k} - t_{k-1}| > 600\text{ seconds (10 minutes)}$$
Each session is aligned independently using the walk geometry algorithm.

---

## 3. Smartphone Camera Naming Standards

In field conditions, photos transferred via WhatsApp, Bluetooth, or SD card adapters frequently have EXIF metadata wiped. However, smartphone operating systems encode chronological information directly into filenames.

```
┌─────────────────────────┬───────────────────────────────┬──────────────────────────────────────────────┐
│ Smartphone / Source     │ Filename Template             │ Chronological & Sequence Signal              │
├─────────────────────────┼───────────────────────────────┼──────────────────────────────────────────────┤
│ Apple iPhone (iOS)      │ IMG_XXXX.JPG / IMG_EXXXX.JPG  │ Monotonic shutter counter: 0001 → 9999       │
│ Samsung Galaxy (OneUI)  │ YYYYMMDD_HHMMSS(_XX).jpg      │ Exact capture timestamp to the second        │
│ Google Pixel (GCam)     │ PXL_YYYYMMDD_HHMMSSxxx.jpg    │ Millisecond precision timestamp              │
│ Tecno / Infinix / Redmi │ IMG_YYYYMMDD_HHMMSS.jpg       │ Embedded capture timestamp                   │
│ WhatsApp Transfer       │ IMG-YYYYMMDD-WAXXXX.jpg       │ Preserves date & monotonic send sequence     │
│ Digital Camera (DCF)    │ DSC_XXXX.JPG / DSC0XXXX.JPG   │ JEITA CP-3461 monotonic sequence counter     │
│ File System Copy        │ plate 1 (1).JPG               │ Parenthesized OS copy index                  │
└─────────────────────────┴───────────────────────────────┴──────────────────────────────────────────────┘
```

### Monotonic Shutter Counter Mirror Law
In an iPhone or WhatsApp roll during a U-turn walk:
- Rears: $R_1, R_2, \dots, R_N$ with sequence numbers $\text{seq}(R_i) = S_0 + i$
- Fronts: $F_1, F_2, \dots, F_N$ with sequence numbers $\text{seq}(F_j) = S_0 + N + j$
- Turnaround occurs at Bike $N$:
  $$\Delta \text{seq}(R_N, F_1) = 1 \quad (\text{Direct consecutive shutter click})$$
  $$\text{seq}(R_i) + \text{seq}(F_{N - i + 1}) = \text{Constant}$$

### Multi-Tier Fallback Timestamp Extraction
To guarantee robust ordering, the extraction hierarchy in `core/services/vault_service.py` is:
1. **Tier 1 (EXIF Tag)**: `DateTimeOriginal` (`0x9003`) or `DateTime` (`0x0132`).
2. **Tier 2 (Filename Timestamp)**: Embedded regex timestamp from Samsung/Pixel/Xiaomi/Tecno (`YYYYMMDD_HHMMSS`).
3. **Tier 3 (Filesystem Mtime)**: File modification time from disk.
4. **Tier 4 (Ingestion Time)**: System clock at batch creation.

---

## 4. Candidate Ranking Formula

When an operator manually reviews an incomplete pair or links a photo in the TUI (`[L]` key), candidate opposite-orientation photos are ranked by composite score $S \in [0, 115]$:

$$S = S_{\text{time}} + S_{\text{text}} + S_{\text{batch}} + S_{\text{sequence}}$$

Where:
- **Time Proximity ($S_{\text{time}} \le 50$)**:
  $$S_{\text{time}} = \max\left(0.0, 50.0 - \frac{\Delta t}{10.0}\right)$$
  Photos taken within 10 seconds receive ~49 points; photos taken >500s away receive 0.
- **Plate Similarity ($S_{\text{text}} \le 40$)**:
  $$S_{\text{text}} = \text{Similarity}(P_{\text{target}}, P_{\text{cand}}) \times 40.0$$
  Character level fuzzy matching with alphanumeric normalization.
- **Batch Affinity ($S_{\text{batch}} = 10$)**:
  $$S_{\text{batch}} = \begin{cases} 10.0, & \text{if candidate in same batch} \\ 0.0, & \text{otherwise} \end{cases}$$
- **Camera Sequence Proximity ($S_{\text{sequence}} \le 15$)**:
  $$S_{\text{sequence}} = \begin{cases} \max(0.0, 15.0 - 1.0 \times |\text{seq}_{\text{target}} - \text{seq}_{\text{cand}}|), & \text{if } |\Delta \text{seq}| \le 15 \\ 0.0, & \text{otherwise} \end{cases}$$

---

## 5. Architectural Implementation Map

```
┌────────────────────────────────────────────────────────────────────────┐
│                          INGESTION PHASE                               │
│  Folder / File Ingestion: front/ and rear/ detected                     │
│  EXIF + Filename Timestamp: core/services/vault_service.py              │
│  Camera Naming Parser:      core/services/camera_naming.py             │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        ASSOCIATION ENGINE                              │
│  Walk Session Clustering:   core/matcher/association.py                │
│  U-Turn / Parallel Detect:  _associate_batch_sequences()               │
│  Candidate Ranking:         get_closest_candidates()                   │
│  Manual Linker:             link_pair_manually()                       │
└──────────────────────────────────┬─────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                     OPERATOR TUI & INTERACTION                         │
│  Closest Photo Picker:      core/tui/dialogs.py (PhotoLinkerModal)     │
│  Keyboard Binding:          [L] in core/tui/app.py & actions.py        │
│  Camera Origin Tagging:     core/tui/inspectors.py (InspectorPane)     │
└────────────────────────────────────────────────────────────────────────┘
```

### Key Source Files

| Module | Primary Responsibility |
| :--- | :--- |
| `core/services/camera_naming.py` | Extracts vendor conventions (`Apple iPhone`, `Samsung`, `Google Pixel`, `WhatsApp`, etc.), embedded timestamps, and sequential counters. |
| `core/services/vault_service.py` | Detects folder orientation, extracts EXIF/filename timestamps, computes SHA-256 hashes, and applies PIL contrast enhancements. |
| `core/matcher/association.py` | Clusters walk sessions, executes U-Turn/Parallel sequence alignment, calculates candidate rankings, and links pairs. |
| `core/tui/dialogs.py` | Provides `PhotoLinkerModal` interactive dialog with keyboard navigation (`↑`/`↓`, `Enter`, `1-9`, `V` preview, `Esc`). |
| `core/tui/actions.py` | Implements `action_link_pair` to trigger the interactive photo picker from the Review Queue. |
| `core/tui/inspectors.py` | Renders camera origin badges (e.g. `[iPhone #41]`, `[Samsung 13:22:50]`) in the inspector panes. |

---

## 6. Verification Results

Evaluated on the production sample dataset of 10 motorcycle photos:

```
--- Association Summary ---
Groups processed : 0
Complete pairs   : 5
Incomplete       : 0
Conflicts        : 0
  Seq (U-turn): UMA607NC linked Front[18c067] + Rear[9a10f9]
  Seq (U-turn): UMA405NC linked Front[048cdb] + Rear[d72edd]
  Seq (U-turn): ANZ711MA linked Front[2a10ee] + Rear[b5b1f6]
  Seq (U-turn): UMA073ND linked Front[2e2739] + Rear[37c97e]
  Seq (U-turn): UMA054ND linked Front[b1ac67] + Rear[599ea8]
```

All 5 motorcycles were matched into complete pairs with zero operator intervention, and operators retain manual linking control via `[L]` in the TUI.
