# Track C Project Summary (handoff for another LLM chat)

**Project:** Privacy-Preserving Distributed mmWave Radar Monitoring for Elderly Care - China
(application title: "Privacy-Preserving mmWave Radar Network for Indoor Safety Monitoring of Elderly People and Children")
**Context:** DKU Extra, Track C. Grant proposal to **EPICS in IEEE (October 2026 call)**, plus a prototype build for a **Maker-Faire demo on Dec 1 (2026)**.
**Newest file:** the procurement list dated 2026-11-03.

## 1. Application facts (form printout dated 29 Sep 2026)
- Applicant: Innovation Hub, Duke Kunshan University (Innovation and Entrepreneurship Initiative). Entity type "Other". IEEE Region 10, Nanjing Section. Country: China. Category: Human Services.
- Faculty advisor: Liqi Ren. Contact on the form: Mr. Minghua Su (the budget file lists the lead as "Minghua Su/Peter").
- Community partner: **Kunshan Welfare Institute** (care home in Kunshan; 167 residents: bedridden elders, 61 in its Mental Health Center, 18 orphaned disabled children). No other partners.
- Team: about 10 students (5 women), 0 non-student IEEE volunteers. Beneficiaries: 165 (short and long term).
- **Requested funds: $6,025** (program maximum $10,000).
- Possible inconsistency: the form says 12 months, but the activity plan and budget text use a 10-month schedule (Jan-Oct 2027). Worth checking.
- EPICS rules: applications 1 Sep - 1 Oct 2026; decisions Jan 2027; spend funds by Jan 2028; final report Jan 2028; project must take 1 year or less; needs at least 1 IEEE member, a community partner, and IEEE-standards-related technology.
- Evaluation criteria: problem statement, partner, technical solution, planning (solution details, budget, timeline, team), student learning outcomes, impact/evaluation plan.
- The QuestionList PDF is the blank form with character limits (Project Description 10,000; Activities 10,000; Innovation 5,000; Safety 5,000; Maintenance 2,000; Ethics 1,000; Budget Justification 5,000).
- Internal task split (WeChat screenshot of a Feishu doc): budget plan and budget justification due Sep 28 (Hafi, Anas); proposal writing due Sep 28: Project Activities and Outputs + Project Innovation (xiaoyao); Ethics Review + Project Impact (Enzo).

## 2. Problem and solution
**Problem:** Care staff are few, so falls and breathing/cardiac emergencies are noticed late. Residents refuse wristbands because they feel like surveillance. Existing radar sensors and patrol robots give false alarms and leave blind spots. "Camera-free" products often upload point clouds to the cloud. A single radar cannot follow a person across rooms or tell residents apart in a shared room.

**Solution:** distributed **60 GHz mmWave radar nodes** plus a **local edge gateway**; no cameras, wearables, audio, face recognition or cloud.

Six listed innovations:
1. Cross-node anonymous-ID handover (timestamps, feature matching, track lifecycle management).
2. Multi-person separation in shared rooms (point-cloud clustering, gait and chest micro-vibration features, ID-bound vital signs).
3. Fall false-alarm suppression: multi-frame filtering, posture recognition, cross-node confirmation, two-tier events (local log vs. caregiver alarm).
4. Privacy by design: local processing, **physical pause button**, anonymization, auto-purge of raw data, audit logs.
5. Sensor-scheme balance: radar is primary; an optional temperature-only infrared module (the system works without it).
6. Personal-baseline physiological anomaly detection: **Phase 2 only**. Phase 1 just stores resting ranges, and only fall alerts go live.

Positioning in the application: Vayyar Care (camera-free, on-premises option) and Neteera (cloud) already exist. The claimed novelty is the integrated, uncertainty-aware, locally maintained system and its measured evaluation, not "camera-free + local" alone.

## 3. Timeline (from the application)
- Months 1-2: requirements, scope, acceptance criteria, confirm 60 GHz legality.
- Months 3-4: build prototype and software (at least 2 nodes), caregiver interface.
- Months 5-6: bench tests, simulated motion, failure tests (**a missing node heartbeat must show a visible gap within 30 s**); partner staff mount nodes.
- Months 7-8: **shadow-mode pilot** (no live alerts; compare with signed staff logs).
- Months 9-10: live alerts only in areas meeting criteria; staff training and alert drill.
- Months 11-12: evaluation and handover (code, install records, spare-parts list, maintenance duties).
- Residents are never asked to simulate falls.

## 4. Safety, ethics, sustainability (key points)
- Risks covered: alarm fatigue and ignored alerts; blind spots and silent node failure; ID misassignment at handover (staff see "tracking uncertain" instead of a guess); consent for residents with dementia (guardian consent, re-confirmed each phase); RF exposure and pacemakers (power below public limits, nurse reviews node positions); mounting done by partner maintenance staff; IEEE reputation; China data rules (no overseas cloud); residual risk of missed falls.
- Alerts go only to dedicated tablets, not personal phones. Quarterly false-alarm and adverse-event statistics go to the partner. No resident data is used in coursework or publications without separate consent.
- Sustainability: three owners (home maintainer, faculty/IEEE representative, local technical partner); monthly log review; spare nodes and a backup gateway; signed USB updates; annual drills; rising pause-switch use treated as a trust warning; Phase 2 alerts only prompt staff checks (no medical claims) and need fresh consent plus a silent trial.
- **Open regulatory issue:** confirm 60 GHz use is permitted (provincial radio authority) before buying radars. Fallback: certified 24 GHz or certified commercial modules, informing EPICS first.

## 5. EPICS budget (USD, total $6,025)
| Category | Subtotal |
|---|---|
| Physical prototype / supplies | $5,128 |
| Local travel (24 visits x 2 cars x $9) | $432 |
| Logistics (240 consent pages $120 + 30 laminated cards $120) | $240 |
| Events (3 sessions x ~25 people x $3) | $225 |

Key supply lines:
- 14 x TI IWR6843AOPEVM radar at $199 (12 nodes + 2 spares); 14 x Raspberry Pi Zero 2 W at $15; microSD $9, power supply $10, USB cable $5 each. The justification groups all of this as "radar sensing", $3,332.
- 2 x Jetson Orin Nano Super at $249 (1 live + 1 spare) with 2 x 500 GB SSD; router $60, switch $25, UPS $75.
- 2 Android tablets at $120; 6 ESP32 pause-switch kits at $20; PETG $88; brackets $60; raceway $80.
- Validation gear: 2 Polar H10 ($180), 2 oximeters ($50), mannequin ($150), crash mat ($80).

No salaries, overhead, laptops, cloud or international travel requested. In-kind: the partner gives installation labor, electricity, wall space and venue; DKU gives lab space, 3D printers, test equipment and laptops. Files: the EPICS Budget xlsx and the Project Budget Justification docx.

## 6. Task 2 - Tech stack validation (docx)
- All parts are off-the-shelf with standard USB/Wi-Fi/Ethernet; no custom hardware. The Jetson only does cross-node fusion, handover and fall judgement; the radar's on-board DSP does point-cloud pre-processing. Resources are enough for 3 nodes.
- **Known pitfall:** TI's official Vital-Signs GUI needs the XDS110 UART on the MMWAVEICBOOST carrier board. The IWR6843AOPEVM uses a CP210x USB-UART, so the GUI won't run out of the box. Options: write a Python serial parser, or buy the MMWAVEICBOOST (optional).
- Risks:
  - The Pi Zero 2 W only has software NTP over Wi-Fi, so timestamp jitter degrades handover. Add a tolerance threshold and don't promise sub-millisecond sync.
  - Vital signs are reliable only for one static person per radar field of view in Phase 1; multi-person tests are for ID separation only.
  - Fall false positives cannot reach zero, so "suspected" events are only logged locally and a formal alarm needs multi-source confirmation.
  - Privacy needs strict audit logging.
- **Maker-Faire Dec 1 demo scope:** presence detection; fall detection with an alarm pop-up on the tablet; physical pause/resume button; single-person static vital signs compared against a Polar H10. **Not demoed:** simultaneous multi-person vitals, personal-baseline anomaly alerts.
- Suggestions: buy the radar from authorized channels (invoice and serial numbers; avoid refurbished or counterfeit boards); use high-endurance microSD; a UPS isn't needed for prototype A but should be added for the pilot phase.

## 7. Prototype procurement (Prototype A, final list 2026-11-03)
Files: `Prototype_Procurement_Final_EN_2026-11-03.xlsx` (current) and `采购清单_可直接采买.xlsx` (older Chinese list, superseded).
- Quantities: 3 radars (IWR6843AOPEVM, Taobao ¥1,680), 3 Pi Zero 2 W (¥180, select "board only"), 3 Samsung PRO Endurance 32 GB, 3 micro-USB power supplies, 3 OTG adapters, 3 brackets.
- Gateway and network: 1 Jetson Orin Nano Super dev kit (¥4,000, includes PSU) + 500 GB NVMe SSD (¥570); TP-Link XDR3010 router, 8-port gigabit switch, Cat6 cables; 2 power strips.
- Other: anchors and cable ties; 2 ESP32-S3 pause kits (button, box, charger, wiring kit); 1 Lenovo Xiaoxin Pad tablet + stand; Polar H10, oximeter, training dummy, crash mats, 3 tripods; 4 spools PETG; optional 64 GB microSD for the Jetson.
- **Computed total: about ¥8,687 including the optional item (¥8,647 without).** The sheet's own total cells were empty, so I summed quantity x unit price. Prices are reference estimates.
- Status labels in the sheet: "Verified" (radar, microSD, PSU, bracket, tie kit); "Pick option" (Pi Zero, OTG, switch, Jetson, anchors, ESP32: the right variant must be selected when ordering); "Double check" (search links for SSD, router, cables, buttons, tablet, testing items).
- Changes versus the old sheet: removed the radar barrel PSU (the radar is powered over USB via the Pi's OTG port) and the Jetson PSU (included in the kit); corrected the Pi price from ¥800 to ¥180; fixed dead or wrong links; added a wiring kit, a second power strip and the optional SD card.
- The sheet also holds A/B/C tiers (prototype / 14-node pilot / 32-node full): radars 3 / 14 / 32, Jetson 1 / 2 / 3, UPS 0 / 1 / 2.

## 8. Open design issues (procurement sheet 4)
1. Wi-Fi (2.4 GHz) for radar nodes and pause buttons, Ethernet only for the Jetson, router and nurse station. **Proposed; needs team OK.**
2. Power: one 5 V micro-USB supply per node runs Pi + radar; needs a bench check for brownouts.
3. Pause button path: ESP32 -> Wi-Fi -> MQTT -> Jetson, with heartbeat; the LED lights only after the Jetson confirms. Team to decide whether a true hardware-level pause (cutting radar power) is required.
4. Enclosure: radar at the front behind a thin PETG window, Pi behind it, no metal in front of the antenna; tilt down for falls/tracking, close range for vitals. To be designed, printed and tested.
5. Reliability tests after delivery: 48 h soak test; unplug a node and the tablet must show a gap within 30 s; reboot router/Jetson and confirm auto-reconnect.

## 9. Things to flag / likely next steps
- The budget uses US list prices (Jetson $249, radar $199), but the Taobao list shows ¥4,000 for the Jetson (about $560) and ¥1,680 per radar (about $236). Revisit pricing if actual purchases differ.
- Duration mismatch: 12 months on the form vs. a 10-month plan.
- Confirm 60 GHz legality, ethics/IRB approval, and the Wi-Fi vs. cable decision.
- Build the Python parser for the AOPEVM vital-sign output, test time-sync offsets, and prepare the Dec 1 demo.

## Files in the folder
EPICS budget xlsx; Budget Justification docx; Task2 - Validate Tech Stack docx; Prototype_Procurement_Final_EN_2026-11-03.xlsx; 采购清单_可直接采买.xlsx; application packet PDF (29 Sep 2026); blank QuestionList PDF; EPICS program info PDF; two Weixin screenshots (form field list and task assignments).
