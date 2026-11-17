"""
ClaimSense — Agentic Motor Insurance Claims Processor
Author : Vishal Gupta (AWS capstone edition) · based on ClaimSense by Shivank Agrawal
File   : scripts/generate_data.py
Purpose: Creates everything the project needs to run, with NO API key:
           1. data/claims.db           -> policies, prior claims, garages (the insurer's core system)
           2. data/sample_claims/...   -> 20 claims, each with PDF documents (claim form, survey, FIR)
           3. data/eval_labels.csv     -> the "correct answer" for every sample claim (for evals)

Run from the project root:
    python -m scripts.generate_data

Each scenario below is hand-written to test one behaviour of the agent:
  CLM-1001..1007 clean, small claims           -> should be AUTO-APPROVED
  CLM-1008..1010 genuine but need a human       -> big amount, medium fraud score, total loss
  CLM-1011..1016 should be REJECTED             -> lapsed policy, TP-only, expired licence,
                                                   drunk driving, ride-hailing use, mechanical breakdown
  CLM-1017..1020 suspicious                     -> should go to the Special Investigation Unit (SIU)
All names, companies, garages and documents are fictional.
"""

import csv
import shutil
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app import config, db, rules

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
GARAGES = [
    ("G01", "Shree Ganesh Motors", "Bengaluru", 0),
    ("G02", "Speedfix Auto Works", "Mumbai", 0),
    ("G03", "Raj Car Care", "Delhi", 0),
    ("G04", "Chennai Auto Hub", "Chennai", 0),
    ("G05", "Deccan Motors", "Hyderabad", 0),
    ("G06", "QuickWheels Garage", "Pune", 1),        # on fraud watchlist
    ("G07", "Royal Auto Body Shop", "Noida", 1),     # on fraud watchlist
    ("G08", "Sabarmati Auto Garage", "Ahmedabad", 0),
    ("G09", "Sahyadri Auto Works", "Pune", 0),
    ("G10", "Awadh Motors", "Lucknow", 0),
    ("G11", "Pink City Auto Care", "Jaipur", 0),
    ("G12", "Millennium Car Clinic", "Gurugram", 0),
    ("G13", "Eastern Auto Services", "Kolkata", 0),
]
GARAGE_NAMES = {g[0]: f"{g[1]}, {g[2]}" for g in GARAGES}

CATEGORY_LABEL = {"metal": "Metal", "plastic_rubber": "Plastic/Rubber", "fibre_glass": "Fibre glass",
                  "glass": "Glass", "labour": "Labour"}


def policy(number, holder, phone, reg, model, cc, reg_date, start, end, idv, zero_dep=0, ptype="COMPREHENSIVE"):
    return dict(policy_number=number, holder_name=holder, phone=phone, vehicle_reg=reg, make_model=model,
                engine_cc=cc, registration_date=reg_date, policy_type=ptype, start_date=start, end_date=end,
                idv=idv, zero_dep=zero_dep)


# ---------------------------------------------------------------------------
# The 20 sample claims (ground truth)
# ---------------------------------------------------------------------------
SCENARIOS = [
    # ------------------------------ AUTO-APPROVE ------------------------------
    dict(claim_id="CLM-1001",
         policy=policy("NS-MOT-240101", "Ananya Rao", "98450 11223", "KA-03-MN-4521", "Maruti Suzuki Swift VXi",
                       1197, "2023-08-14", "2026-08-14", "2027-08-13", 520000),
         prior_claims=[],
         form=dict(loss_date="2026-10-21", loss_time="18:40", location="Outer Ring Road, Marathahalli, Bengaluru",
                   intimation_date="2026-10-22", driver="Ananya Rao", licence_no="KA03 20150012345",
                   licence_valid_till="2035-06-30", use="Driving back home from office",
                   description="Traffic slowed suddenly near the Marathahalli bridge and I could not stop in time. "
                               "My car's front hit the rear of a stationary truck at low speed. Nobody was hurt.",
                   damaged_areas=["front bumper", "left headlamp"], fir=False, witness="Yes - Mr. Vikram S. (co-passenger)",
                   garage="G01", estimate=22500),
         survey=dict(surveyor="R. Krishnamurthy", inspection_date="2026-10-23", damaged_areas=["front bumper", "left headlamp", "bumper bracket"],
                     observations="Fresh impact marks on the front bumper. Left headlamp lens and housing cracked. "
                                  "Bumper mounting bracket bent. Damage pattern matches a low-speed frontal impact.",
                     consistent=True,
                     items=[("Front bumper", "plastic_rubber", 6500), ("Left headlamp assembly", "plastic_rubber", 8200),
                            ("Front bumper bracket", "metal", 1200), ("Labour and painting", "labour", 4800)]),
         fir=None, expected=("AUTO_APPROVE", "APPROVE", False)),

    dict(claim_id="CLM-1002",
         policy=policy("NS-MOT-240102", "Rohan Kulkarni", "98220 44556", "MH-12-QR-7788", "Hyundai Creta SX",
                       1497, "2025-02-10", "2026-02-10", "2027-02-09", 1350000, zero_dep=1),
         prior_claims=[],
         form=dict(loss_date="2026-10-05", loss_time="10:15", location="Society basement parking, Baner, Pune",
                   intimation_date="2026-10-05", driver="Rohan Kulkarni", licence_no="MH12 20120098765",
                   licence_valid_till="2032-03-14", use="Parking the car at home",
                   description="While reversing into my parking slot in the basement, the right rear side of the car "
                               "scraped against a concrete pillar.",
                   damaged_areas=["right rear door", "right rear quarter panel"], fir=False,
                   witness="Yes - Society security guard", garage="G09", estimate=31000),
         survey=dict(surveyor="Meera Joshi", inspection_date="2026-10-06", damaged_areas=["right rear door", "right rear quarter panel"],
                     observations="Long horizontal scrape with concrete dust on the right rear door and quarter panel. "
                                  "Door needs replacement; quarter panel can be repaired.",
                     consistent=True,
                     items=[("Right rear door shell", "metal", 14500), ("Rear quarter panel repair", "metal", 6000),
                            ("Labour and painting", "labour", 9500)]),
         fir=None, expected=("AUTO_APPROVE", "APPROVE", False)),

    dict(claim_id="CLM-1003",
         policy=policy("NS-MOT-240103", "Sandeep Malhotra", "98110 77889", "DL-8C-AF-3310", "Tata Nexon XZ+",
                       1199, "2022-01-20", "2026-01-20", "2027-01-19", 680000),
         prior_claims=[("2026-03-02", 12500)],
         form=dict(loss_date="2026-11-02", loss_time="08:50", location="Sector 11 main road, Rohini, Delhi",
                   intimation_date="2026-11-03", driver="Sandeep Malhotra", licence_no="DL08 20100045612",
                   licence_valid_till="2030-09-01", use="Dropping my daughter to school",
                   description="A stray cow suddenly crossed the road. I braked hard but hit the animal. "
                               "The bonnet got dented and the front grille broke.",
                   damaged_areas=["bonnet", "front grille"], fir=False, witness="Yes - my daughter and a shopkeeper",
                   garage="G03", estimate=21800),
         survey=dict(surveyor="Arvind Sethi", inspection_date="2026-11-04", damaged_areas=["bonnet", "front grille"],
                     observations="Rounded dent at the centre of the bonnet and broken grille, typical of an animal hit. "
                                  "No damage to radiator or engine components.",
                     consistent=True,
                     items=[("Bonnet", "metal", 11000), ("Front grille", "plastic_rubber", 3400),
                            ("Labour and painting", "labour", 6200)]),
         fir=None, expected=("AUTO_APPROVE", "APPROVE", False)),

    dict(claim_id="CLM-1004",
         policy=policy("NS-MOT-240104", "Lakshmi Narayanan", "94440 12121", "TN-09-BX-2045", "Honda City VX",
                       1498, "2024-06-18", "2026-06-18", "2027-06-17", 1100000),
         prior_claims=[],
         form=dict(loss_date="2026-10-28", loss_time="14:30", location="Outside my house, T. Nagar, Chennai",
                   intimation_date="2026-10-29", driver="Not applicable - car was parked", licence_no="TN09 20080033221",
                   licence_valid_till="2031-11-20", use="Car was parked outside home",
                   description="During heavy rain and strong winds, a large branch from a roadside tree fell on my "
                               "parked car. The windshield cracked and the roof got dented.",
                   damaged_areas=["windshield", "roof"], fir=False, witness="Yes - neighbours", garage="G04", estimate=29000),
         survey=dict(surveyor="K. Venkatesh", inspection_date="2026-10-30", damaged_areas=["windshield", "roof"],
                     observations="Windshield shattered with a branch impact point. Two dents on the roof panel. "
                                  "Tree branch still visible at site. Consistent with storm damage.",
                     consistent=True,
                     items=[("Windshield glass", "glass", 18500), ("Roof panel denting", "metal", 5500),
                            ("Labour", "labour", 4000)]),
         fir=None, expected=("AUTO_APPROVE", "APPROVE", False)),

    dict(claim_id="CLM-1005",
         policy=policy("NS-MOT-240105", "Farhan Siddiqui", "99490 56565", "TS-09-EZ-6612", "Kia Seltos HTX",
                       1493, "2025-11-05", "2025-11-05", "2026-11-04", 1400000),
         prior_claims=[],
         form=dict(loss_date="2026-10-12", loss_time="19:20", location="Mall parking, Hitech City, Hyderabad",
                   intimation_date="2026-10-13", driver="Farhan Siddiqui", licence_no="TS09 20160011122",
                   licence_valid_till="2036-02-18", use="Weekend shopping with family",
                   description="Another car reversing out of its parking spot hit my car's left front door while "
                               "I was parked.", damaged_areas=["left front door"], fir=False,
                   witness="Yes - parking attendant", garage="G05", estimate=27500),
         survey=dict(surveyor="P. Srinivas", inspection_date="2026-10-14", damaged_areas=["left front door", "door moulding"],
                     observations="Deep dent on the left front door with foreign paint transfer. Door moulding broken.",
                     consistent=True,
                     items=[("Left front door", "metal", 16000), ("Door moulding", "plastic_rubber", 2200),
                            ("Labour and painting", "labour", 7800)]),
         fir=None, expected=("AUTO_APPROVE", "APPROVE", False)),

    dict(claim_id="CLM-1006",
         policy=policy("NS-MOT-240106", "Priya Menon", "98200 34343", "MH-02-FG-9001", "Mahindra XUV700 AX7",
                       1997, "2023-03-15", "2026-03-15", "2027-03-14", 1700000, zero_dep=1),
         prior_claims=[],
         form=dict(loss_date="2026-10-30", loss_time="21:10", location="Western Express Highway, Andheri East, Mumbai",
                   intimation_date="2026-10-31", driver="Priya Menon", licence_no="MH02 20110076543",
                   licence_valid_till="2031-05-09", use="Returning home from a family dinner",
                   description="I was stopped at a traffic signal when a taxi hit my car from behind and drove away. "
                               "Rear bumper, tailgate and right tail lamp were damaged. I filed a police complaint.",
                   damaged_areas=["rear bumper", "tailgate", "right tail lamp"], fir=True,
                   witness="Yes - other motorists", garage="G02", estimate=47000),
         survey=dict(surveyor="Nikhil Desai", inspection_date="2026-11-01", damaged_areas=["rear bumper", "tailgate", "right tail lamp"],
                     observations="Rear-end impact. Rear bumper cracked, tailgate dented, right tail lamp broken. "
                                  "Yellow paint transfer consistent with a taxi.",
                     consistent=True,
                     items=[("Rear bumper", "plastic_rubber", 12000), ("Right tail lamp", "plastic_rubber", 9500),
                            ("Tailgate", "metal", 15000), ("Labour and painting", "labour", 8500)]),
         fir=dict(fir_number="AND/2026/1187", station="Andheri Police Station", date="2026-10-30", time="21:10",
                  location="Western Express Highway near Andheri signal",
                  summary="Complainant Priya Menon reported that an unknown taxi hit her car from behind at the "
                          "signal and fled. Rear damage to the complainant's vehicle noted. No injuries.",
                  breath="Not tested - complainant was not at fault", intoxicated=False),
         expected=("AUTO_APPROVE", "APPROVE", False)),

    dict(claim_id="CLM-1007",
         policy=policy("NS-MOT-240107", "Hetal Patel", "98250 90909", "GJ-01-KP-4410", "Maruti Suzuki Baleno Delta",
                       1197, "2019-07-01", "2026-07-01", "2027-06-30", 410000),
         prior_claims=[],
         form=dict(loss_date="2026-11-04", loss_time="11:00", location="Narrow lane near Maninagar station, Ahmedabad",
                   intimation_date="2026-11-04", driver="Hetal Patel", licence_no="GJ01 20090054321",
                   licence_valid_till="2029-12-31", use="Going to the bank",
                   description="A city bus squeezed past my car in a narrow lane and scraped the right side. "
                               "The right mirror broke and the right front door got scratched and dented.",
                   damaged_areas=["right side mirror", "right front door"], fir=False, witness="Yes - shop owner",
                   garage="G08", estimate=20000),
         survey=dict(surveyor="J. Shah", inspection_date="2026-11-05", damaged_areas=["right side mirror", "right front door"],
                     observations="Scrape marks along the right side at bus height. Mirror housing broken.",
                     consistent=True,
                     items=[("Right outer rear-view mirror", "plastic_rubber", 4600), ("Right front door", "metal", 9000),
                            ("Labour and painting", "labour", 5400)]),
         fir=None, expected=("AUTO_APPROVE", "APPROVE", False)),

    # --------------------------- HUMAN REVIEW, GENUINE ---------------------------
    dict(claim_id="CLM-1008",  # payable above auto-approve limit
         policy=policy("NS-MOT-240108", "Girish Hegde", "98860 22334", "KA-05-MR-7070", "Toyota Innova Crysta",
                       2393, "2024-12-01", "2025-12-01", "2026-11-30", 2100000, zero_dep=1),
         prior_claims=[],
         form=dict(loss_date="2026-10-16", loss_time="16:45", location="NICE Road near Hosur Road exit, Bengaluru",
                   intimation_date="2026-10-16", driver="Girish Hegde", licence_no="KA05 20070022113",
                   licence_valid_till="2033-08-19", use="Family trip to Mysuru",
                   description="It was raining heavily. The car skidded on the wet road and hit the median. "
                               "The front of the car was badly damaged. Airbags did not open and no one was hurt.",
                   damaged_areas=["front bumper", "bonnet", "radiator", "left headlamp", "front grille"], fir=False,
                   witness="Yes - wife and son (passengers)", garage="G01", estimate=96000),
         survey=dict(surveyor="R. Krishnamurthy", inspection_date="2026-10-17",
                     damaged_areas=["front bumper", "bonnet", "radiator", "left headlamp", "front grille"],
                     observations="Heavy frontal damage with median paint and concrete marks. Radiator core pushed in. "
                                  "Consistent with a skid into the median.",
                     consistent=True,
                     items=[("Front bumper", "plastic_rubber", 14000), ("Bonnet", "metal", 22000),
                            ("Radiator", "metal", 18500), ("Left headlamp assembly", "plastic_rubber", 16500),
                            ("Front grille", "plastic_rubber", 6000), ("Labour and painting", "labour", 15000)]),
         fir=None, expected=("ADJUSTER_REVIEW", "APPROVE", False)),

    dict(claim_id="CLM-1009",  # medium fraud score but genuine
         policy=policy("NS-MOT-240109", "Kunal Bansal", "98100 66778", "DL-3C-CA-5566", "Hyundai i20 Asta",
                       1197, "2021-09-10", "2026-01-20", "2027-01-19", 560000),
         prior_claims=[("2026-02-11", 18000), ("2026-06-03", 9500)],
         form=dict(loss_date="2026-10-25", loss_time="23:40", location="Sector 21 road, Dwarka, Delhi",
                   intimation_date="2026-10-26", driver="Kunal Bansal", licence_no="DL03 20140067890",
                   licence_valid_till="2034-04-22", use="Returning from a family function",
                   description="I swerved to avoid a large pothole and the front of the car hit the road divider. "
                               "Front bumper and right fog lamp were damaged.",
                   damaged_areas=["front bumper", "right fog lamp"], fir=False, witness="No - I was alone",
                   garage="G03", estimate=15500),
         survey=dict(surveyor="Arvind Sethi", inspection_date="2026-10-27", damaged_areas=["front bumper", "right fog lamp"],
                     observations="Front bumper cracked on the right with divider paint marks. Right fog lamp broken.",
                     consistent=True,
                     items=[("Front bumper", "plastic_rubber", 7800), ("Right fog lamp", "plastic_rubber", 2600),
                            ("Labour and painting", "labour", 4100)]),
         fir=None, expected=("ADJUSTER_REVIEW", "APPROVE", False)),

    dict(claim_id="CLM-1010",  # constructive total loss (flood)
         policy=policy("NS-MOT-240110", "Revathi Subramanian", "94450 78787", "TN-22-DM-8123", "Maruti Suzuki Dzire VXi",
                       1197, "2017-05-20", "2026-05-20", "2027-05-19", 240000),
         prior_claims=[],
         form=dict(loss_date="2026-11-08", loss_time="03:00", location="Velachery, Chennai",
                   intimation_date="2026-11-09", driver="Not applicable - car was parked", licence_no="TN22 20050011987",
                   licence_valid_till="2030-01-15", use="Car was parked at home",
                   description="Heavy overnight rain flooded our street. The car parked outside was submerged up to "
                               "the dashboard. It does not start now.",
                   damaged_areas=["engine", "electrical wiring", "interior"], fir=False, witness="Yes - neighbours",
                   garage="G04", estimate=225000),
         survey=dict(surveyor="K. Venkatesh", inspection_date="2026-11-10", damaged_areas=["engine", "wiring harness and ECU", "interior upholstery"],
                     observations="Water line up to the dashboard. Engine hydrostatically locked, wiring harness and ECU "
                                  "corroded, carpets and seats soaked. Damage caused by flood inundation.",
                     consistent=True,
                     items=[("Engine replacement", "metal", 125000), ("Wiring harness and ECU", "plastic_rubber", 48000),
                            ("Interior upholstery and carpets", "plastic_rubber", 22000), ("Labour", "labour", 18000)]),
         fir=None, expected=("ADJUSTER_REVIEW", "APPROVE", False)),

    # ------------------------------ SHOULD BE REJECTED ------------------------------
    dict(claim_id="CLM-1011",  # policy expired before the accident
         policy=policy("NS-MOT-230111", "Amit Srivastava", "94150 23232", "UP-32-LM-2290", "Tata Punch Accomplished",
                       1199, "2024-01-10", "2025-09-01", "2026-08-31", 620000),
         prior_claims=[],
         form=dict(loss_date="2026-10-09", loss_time="12:30", location="Hazratganj, Lucknow",
                   intimation_date="2026-10-10", driver="Amit Srivastava", licence_no="UP32 20130044556",
                   licence_valid_till="2033-07-07", use="Office work",
                   description="An e-rickshaw hit the left side of my car in traffic. Left rear door dented.",
                   damaged_areas=["left rear door"], fir=False, witness="Yes - colleague", garage="G10", estimate=14000),
         survey=dict(surveyor="S. Tiwari", inspection_date="2026-10-11", damaged_areas=["left rear door"],
                     observations="Dent on the left rear door at e-rickshaw bumper height.", consistent=True,
                     items=[("Left rear door repair", "metal", 7000), ("Labour and painting", "labour", 5500)]),
         fir=None, expected=("ADJUSTER_REVIEW", "REJECT", False)),

    dict(claim_id="CLM-1012",  # third-party-only policy
         policy=policy("NS-MOT-240112", "Mahesh Choudhary", "94140 45454", "RJ-14-CV-7731", "Maruti Suzuki Ertiga ZXi",
                       1462, "2022-08-25", "2026-08-25", "2027-08-24", 800000, ptype="THIRD_PARTY"),
         prior_claims=[],
         form=dict(loss_date="2026-10-18", loss_time="17:05", location="Tonk Road, Jaipur",
                   intimation_date="2026-10-19", driver="Mahesh Choudhary", licence_no="RJ14 20100033445",
                   licence_valid_till="2030-10-10", use="Going to a relative's house",
                   description="A truck changing lanes hit the right side of my car. Right doors and mirror damaged. "
                               "I filed an FIR against the truck driver.",
                   damaged_areas=["right front door", "right rear door", "right mirror"], fir=True,
                   witness="Yes - traffic constable", garage="G11", estimate=42000),
         survey=dict(surveyor="L. Sharma", inspection_date="2026-10-20", damaged_areas=["right front door", "right rear door", "right mirror"],
                     observations="Side-swipe damage along the right side at truck height.", consistent=True,
                     items=[("Right front door", "metal", 13000), ("Right rear door", "metal", 12000),
                            ("Right mirror", "plastic_rubber", 4200), ("Labour and painting", "labour", 9000)]),
         fir=dict(fir_number="JPR/2026/3321", station="Ashok Nagar Police Station", date="2026-10-18", time="17:05",
                  location="Tonk Road, Jaipur", summary="Truck driver changed lanes without signalling and hit the "
                  "complainant's car. Truck driver identified. No injuries.", breath="0 mg/100 ml (complainant)",
                  intoxicated=False),
         expected=("ADJUSTER_REVIEW", "REJECT", False)),

    dict(claim_id="CLM-1013",  # driver's licence expired
         policy=policy("NS-MOT-240113", "Suresh Iyer", "98450 67676", "KA-51-HJ-3388", "Skoda Slavia Ambition",
                       999, "2023-05-12", "2026-05-12", "2027-05-11", 1150000),
         prior_claims=[],
         form=dict(loss_date="2026-10-27", loss_time="16:10", location="Electronic City Phase 1, Bengaluru",
                   intimation_date="2026-10-28", driver="Aditya Iyer (insured's son)", licence_no="KA51 20060099887",
                   licence_valid_till="2026-07-31", use="Going to college",
                   description="My son was driving when a car in front braked suddenly and he hit it. Front bumper and "
                               "bonnet damaged.", damaged_areas=["front bumper", "bonnet"], fir=False,
                   witness="Yes - friend in the car", garage="G01", estimate=28000),
         survey=dict(surveyor="Meera Joshi", inspection_date="2026-10-29", damaged_areas=["front bumper", "bonnet"],
                     observations="Frontal impact, bumper cracked, bonnet edge dented.", consistent=True,
                     items=[("Front bumper", "plastic_rubber", 9000), ("Bonnet", "metal", 11000),
                            ("Labour and painting", "labour", 6500)]),
         fir=None, expected=("ADJUSTER_REVIEW", "REJECT", False)),

    dict(claim_id="CLM-1014",  # drunk driving (only the FIR reveals it)
         policy=policy("NS-MOT-240114", "Varun Khanna", "98110 13579", "HR-26-EK-4512", "Volkswagen Virtus GT",
                       1498, "2024-03-08", "2026-03-08", "2027-03-07", 1300000),
         prior_claims=[],
         form=dict(loss_date="2026-10-31", loss_time="23:15", location="Near Cyber Hub, DLF Phase 2, Gurugram",
                   intimation_date="2026-11-01", driver="Varun Khanna", licence_no="HR26 20120011335",
                   licence_valid_till="2032-02-28", use="Returning from a friend's party",
                   description="I lost control on a turn and hit a parked car. Front bumper, bonnet and right headlamp "
                               "damaged. Police came to the spot.",
                   damaged_areas=["front bumper", "bonnet", "right headlamp"], fir=True, witness="Yes - security guard",
                   garage="G12", estimate=52000),
         survey=dict(surveyor="Arvind Sethi", inspection_date="2026-11-02", damaged_areas=["front bumper", "bonnet", "right headlamp"],
                     observations="Frontal impact into a stationary vehicle. Damage consistent with the description.",
                     consistent=True,
                     items=[("Front bumper", "plastic_rubber", 11000), ("Bonnet", "metal", 17000),
                            ("Right headlamp assembly", "plastic_rubber", 14000), ("Labour and painting", "labour", 8000)]),
         fir=dict(fir_number="GGM/2026/7745", station="DLF Phase 2 Police Station", date="2026-10-31", time="23:15",
                  location="Near Cyber Hub, DLF Phase 2", summary="Driver Varun Khanna hit a parked car. Breath "
                  "analyser test conducted at the spot. Driver booked under Section 185 of the Motor Vehicles Act "
                  "for drunken driving.", breath="78 mg/100 ml", intoxicated=True),
         expected=("ADJUSTER_REVIEW", "REJECT", False)),

    dict(claim_id="CLM-1015",  # private car used for paid ride-hailing
         policy=policy("NS-MOT-240115", "Naveen Gowda", "99000 24680", "KA-01-AK-6633", "Honda Amaze VX",
                       1199, "2021-04-22", "2026-04-22", "2027-04-21", 550000),
         prior_claims=[],
         form=dict(loss_date="2026-10-19", loss_time="20:05", location="Sony World junction, Koramangala, Bengaluru",
                   intimation_date="2026-10-20", driver="Naveen Gowda", licence_no="KA01 20110088776",
                   licence_valid_till="2031-03-03", use="Dropping a passenger who booked a ride on the RideNow app "
                                                       "(I drive on the app on weekends to earn extra income)",
                   description="A motorbike rammed into my left rear door at the junction while I was dropping a "
                               "passenger.", damaged_areas=["left rear door"], fir=False,
                   witness="Yes - the passenger", garage="G01", estimate=17000),
         survey=dict(surveyor="R. Krishnamurthy", inspection_date="2026-10-21", damaged_areas=["left rear door"],
                     observations="Dent on the left rear door at motorbike handlebar height.", consistent=True,
                     items=[("Left rear door", "metal", 9500), ("Labour and painting", "labour", 5500)]),
         fir=None, expected=("ADJUSTER_REVIEW", "REJECT", False)),

    dict(claim_id="CLM-1016",  # mechanical breakdown, not an accident
         policy=policy("NS-MOT-240116", "Debashis Ghosh", "98300 11447", "WB-02-AL-9087", "Hyundai Venue SX",
                       998, "2020-08-30", "2026-08-30", "2027-08-29", 600000),
         prior_claims=[],
         form=dict(loss_date="2026-10-24", loss_time="13:30", location="EM Bypass near Ruby crossing, Kolkata",
                   intimation_date="2026-10-24", driver="Debashis Ghosh", licence_no="WB02 20040066554",
                   licence_valid_till="2029-05-05", use="Going to a client meeting",
                   description="The engine suddenly made a loud knocking noise and stopped. The oil warning light had "
                               "been on for a few days. The car had to be towed to the garage.",
                   damaged_areas=["engine"], fir=False, witness="No", garage="G13", estimate=82000),
         survey=dict(surveyor="A. Banerjee", inspection_date="2026-10-25", damaged_areas=["engine"],
                     observations="Engine seized due to oil starvation. Oil sump intact, no external impact marks on the "
                                  "vehicle. This is a mechanical breakdown from lack of lubrication.",
                     consistent=True,
                     items=[("Engine block and crankshaft", "metal", 68000), ("Labour", "labour", 12000)]),
         fir=None, expected=("ADJUSTER_REVIEW", "REJECT", False)),

    # ------------------------------ SUSPICIOUS -> SIU ------------------------------
    dict(claim_id="CLM-1017",  # new policy + watchlist garage + inflated estimate
         policy=policy("NS-MOT-260117", "Rakesh Pawar", "98230 55441", "MH-14-TT-5050", "Mahindra Scorpio-N Z8",
                       2184, "2022-04-10", "2026-10-01", "2027-09-30", 1500000),
         prior_claims=[],
         form=dict(loss_date="2026-10-13", loss_time="15:20", location="Expressway service road, Talegaon, Pune",
                   intimation_date="2026-10-14", driver="Rakesh Pawar", licence_no="MH14 20090077889",
                   licence_valid_till="2034-12-12", use="Business trip to Mumbai",
                   description="While overtaking, my car hit a tempo. The front bumper, bonnet and both headlamps were "
                               "damaged.", damaged_areas=["front bumper", "bonnet", "both headlamps"], fir=False,
                   witness="Yes - driver of the tempo", garage="G06", estimate=145000),
         survey=dict(surveyor="Meera Joshi", inspection_date="2026-10-15", damaged_areas=["front bumper", "bonnet", "both headlamps"],
                     observations="Frontal damage present and consistent with a collision. Garage estimate includes "
                                  "parts that are not damaged (radiator, AC condenser).",
                     consistent=True,
                     items=[("Front bumper", "plastic_rubber", 15000), ("Bonnet", "metal", 24000),
                            ("Both headlamp assemblies", "plastic_rubber", 22000), ("Labour and painting", "labour", 11000)]),
         fir=None, expected=("SIU_INVESTIGATION", "APPROVE", True)),

    dict(claim_id="CLM-1018",  # repeat claimant + late + unwitnessed night accident + inflated estimate
         policy=policy("NS-MOT-250118", "Deepak Chauhan", "98710 99001", "UP-16-DK-8008", "Tata Harrier XZA+",
                       1956, "2023-01-05", "2025-12-20", "2026-12-19", 1750000),
         prior_claims=[("2026-01-14", 42000), ("2026-05-29", 31000)],
         form=dict(loss_date="2026-10-03", loss_time="01:15", location="Empty road near Sector 62, Noida",
                   intimation_date="2026-10-21", driver="Deepak Chauhan", licence_no="UP16 20150022334",
                   licence_valid_till="2035-01-01", use="Returning home late from work",
                   description="I swerved to avoid a dog and hit an electric pole. The front and left side of the car "
                               "were damaged. I was busy and could not report it earlier.",
                   damaged_areas=["front bumper", "left front fender", "left headlamp"], fir=False,
                   witness="No - I was alone", garage="G03", estimate=120000),
         survey=dict(surveyor="Arvind Sethi", inspection_date="2026-10-22", damaged_areas=["front bumper", "left front fender", "left headlamp"],
                     observations="Front-left damage consistent with a pole impact. Repairs appear to have been started "
                                  "before inspection.",
                     consistent=True,
                     items=[("Front bumper", "plastic_rubber", 16000), ("Left front fender", "metal", 21000),
                            ("Left headlamp assembly", "plastic_rubber", 25000), ("Labour and painting", "labour", 16000)]),
         fir=None, expected=("SIU_INVESTIGATION", "APPROVE", True)),

    dict(claim_id="CLM-1019",  # story says rear hit, surveyor finds front damage + watchlist garage
         policy=policy("NS-MOT-240119", "Imran Qureshi", "98990 31313", "UP-16-BZ-1212", "Hyundai Creta SX(O)",
                       1497, "2024-02-15", "2026-02-15", "2027-02-14", 1250000),
         prior_claims=[],
         form=dict(loss_date="2026-10-26", loss_time="19:45", location="Red light near Sector 18 metro, Noida",
                   intimation_date="2026-10-27", driver="Imran Qureshi", licence_no="UP16 20120055667",
                   licence_valid_till="2032-06-06", use="Going to the market",
                   description="I was stopped at a red light when a speeding car rammed into the back of my car. The "
                               "rear bumper, boot lid and tail lamps were damaged.",
                   damaged_areas=["rear bumper", "boot lid", "tail lamps"], fir=False, witness="Yes - a passer-by",
                   garage="G07", estimate=58000),
         survey=dict(surveyor="Arvind Sethi", inspection_date="2026-10-28", damaged_areas=["front bumper", "bonnet", "radiator support"],
                     observations="Fresh damage is on the FRONT of the vehicle: front bumper, bonnet and radiator support. "
                                  "The rear bumper and boot lid show only old minor scratches. Damage pattern indicates a "
                                  "frontal impact, which is not consistent with the rear-end collision described.",
                     consistent=False,
                     items=[("Front bumper", "plastic_rubber", 13000), ("Bonnet", "metal", 19000),
                            ("Radiator support panel", "metal", 9000), ("Labour and painting", "labour", 11000)]),
         fir=None, expected=("SIU_INVESTIGATION", "APPROVE", True)),

    dict(claim_id="CLM-1020",  # FIR shows the accident happened BEFORE the policy was bought
         policy=policy("NS-MOT-260120", "Sneha Reddy", "99860 42424", "KA-04-NP-7717", "Maruti Suzuki Grand Vitara Zeta",
                       1462, "2023-06-30", "2026-09-28", "2027-09-27", 1180000),
         prior_claims=[],
         form=dict(loss_date="2026-10-14", loss_time="09:30", location="Hebbal flyover, Bengaluru",
                   intimation_date="2026-10-15", driver="Sneha Reddy", licence_no="KA04 20130011221",
                   licence_valid_till="2033-09-09", use="Going to office",
                   description="My car collided with an auto-rickshaw on the Hebbal flyover. The front bumper and right "
                               "headlamp were damaged. The auto driver had minor injuries so an FIR was filed.",
                   damaged_areas=["front bumper", "right headlamp"], fir=True, witness="Yes - auto passenger",
                   garage="G01", estimate=26000),
         survey=dict(surveyor="R. Krishnamurthy", inspection_date="2026-10-16", damaged_areas=["front bumper", "right headlamp"],
                     observations="Frontal damage on the right side consistent with a collision with a small vehicle.",
                     consistent=True,
                     items=[("Front bumper", "plastic_rubber", 9500), ("Right headlamp assembly", "plastic_rubber", 9800),
                            ("Labour and painting", "labour", 5200)]),
         fir=dict(fir_number="HBL/2026/2219", station="Hebbal Traffic Police Station", date="2026-09-24", time="09:30",
                  location="Hebbal flyover, Bengaluru", summary="Car KA-04-NP-7717 collided with an auto-rickshaw. "
                  "Auto driver suffered minor injuries and was treated at a nearby clinic.",
                  breath="0 mg/100 ml", intoxicated=False),
         expected=("SIU_INVESTIGATION", "APPROVE", True)),
]

# Extra policies with no claim (so the policy table looks like a real book of business)
FILLER_POLICIES = [
    policy("NS-MOT-240201", "Pooja Agarwal", "98300 22110", "WB-06-CD-1188", "Tata Tiago XZ", 1199, "2022-02-02", "2026-02-02", "2027-02-01", 420000),
    policy("NS-MOT-240202", "Manoj Pillai", "94470 33221", "KL-07-CQ-3456", "Toyota Glanza G", 1197, "2023-09-09", "2026-09-09", "2027-09-08", 610000, zero_dep=1),
    policy("NS-MOT-240203", "Ritu Saxena", "98180 44332", "DL-1C-AB-7788", "MG Hector Sharp", 1451, "2022-12-12", "2025-12-12", "2026-12-11", 1300000),
    policy("NS-MOT-240204", "Arjun Reddy", "99490 55443", "TS-08-GH-9900", "Toyota Fortuner 4x2", 2755, "2021-06-06", "2026-06-06", "2027-06-05", 2600000),
    policy("NS-MOT-240205", "Kavita Nair", "98950 66554", "KL-01-BB-2233", "Maruti Suzuki WagonR VXi", 998, "2020-03-03", "2026-03-03", "2027-03-02", 290000, ptype="THIRD_PARTY"),
]


# ---------------------------------------------------------------------------
# PDF helpers (ReportLab). Documents use Indian formats on purpose: DD/MM/YYYY dates,
# 12-hour times, "Rs. 22,500" amounts -> the LLM must normalise them during extraction.
# ---------------------------------------------------------------------------
STYLES = getSampleStyleSheet()


def ddmmyyyy(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def ampm(hhmm: str) -> str:
    h, m = map(int, hhmm.split(":"))
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def rupees(amount: float) -> str:
    return f"Rs. {amount:,.0f}"


def kv_table(rows):
    table = Table([[Paragraph(f"<b>{k}</b>", STYLES["Normal"]), Paragraph(str(v), STYLES["Normal"])] for k, v in rows],
                  colWidths=[55 * mm, 115 * mm])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke)]))
    return table


def write_pdf(path, title, subtitle, blocks):
    doc = SimpleDocTemplate(str(path), pagesize=A4, topMargin=15 * mm, bottomMargin=15 * mm)
    story = [Paragraph("SAMPLE DOCUMENT - FICTIONAL - FOR TEACHING ONLY", STYLES["Italic"]),
             Paragraph(title, STYLES["Title"]), Paragraph(subtitle, STYLES["Normal"]), Spacer(1, 6 * mm)]
    for block in blocks:
        story += [block, Spacer(1, 4 * mm)]
    doc.build(story)


def make_claim_form(s, folder):
    p, f = s["policy"], s["form"]
    write_pdf(folder / "claim_form.pdf", "NorthStar General Insurance - Motor Claim Form",
              f"Claim reference: {s['claim_id']}", [
        Paragraph("<b>A. Policy and vehicle</b>", STYLES["Heading3"]),
        kv_table([("Policy number", p["policy_number"]), ("Insured name", p["holder_name"]),
                  ("Mobile", p["phone"]), ("Vehicle registration no.", p["vehicle_reg"]),
                  ("Make / model", p["make_model"])]),
        Paragraph("<b>B. Accident details</b>", STYLES["Heading3"]),
        kv_table([("Date of accident", ddmmyyyy(f["loss_date"])), ("Time of accident", ampm(f["loss_time"])),
                  ("Place of accident", f["location"]), ("Date claim reported", ddmmyyyy(f["intimation_date"])),
                  ("Purpose of journey / use of vehicle", f["use"]), ("How did the accident happen?", f["description"]),
                  ("Damaged parts (as per insured)", ", ".join(f["damaged_areas"]))]),
        Paragraph("<b>C. Driver details</b>", STYLES["Heading3"]),
        kv_table([("Driver name", f["driver"]), ("Driving licence no.", f["licence_no"]),
                  ("Licence valid till", ddmmyyyy(f["licence_valid_till"]))]),
        Paragraph("<b>D. Police, witness and garage</b>", STYLES["Heading3"]),
        kv_table([("FIR filed?", "Yes" if f["fir"] else "No"), ("Witness available?", f["witness"]),
                  ("Garage code / name", f"{f['garage']} - {GARAGE_NAMES[f['garage']]}"),
                  ("Garage repair estimate", rupees(f["estimate"]))]),
        Paragraph("I declare that the information given above is true to the best of my knowledge. "
                  f"<br/><br/>Signature: ____________ ({p['holder_name']})", STYLES["Normal"]),
    ])


def make_survey_report(s, folder):
    p, sv, f = s["policy"], s["survey"], s["form"]
    rows = [["S.No", "Description", "Category", "Assessed amount"]]
    for i, (desc, cat, amt) in enumerate(sv["items"], 1):
        rows.append([str(i), desc, CATEGORY_LABEL[cat], rupees(amt)])
    total = sum(a for _, _, a in sv["items"])
    rows.append(["", "Total assessed (before depreciation)", "", rupees(total)])
    table = Table(rows, colWidths=[12 * mm, 80 * mm, 35 * mm, 40 * mm])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                               ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                               ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    opinion = "IS" if sv["consistent"] else "IS NOT"
    write_pdf(folder / "survey_report.pdf", "Precision Loss Assessors - Motor Survey Report",
              f"Claim reference: {s['claim_id']} | Insurer: NorthStar General Insurance", [
        kv_table([("Surveyor", f"{sv['surveyor']} (Licensed surveyor, sample licence SLA-{s['claim_id'][-4:]})"),
                  ("Date of inspection", ddmmyyyy(sv["inspection_date"])), ("Vehicle", f"{p['vehicle_reg']} - {p['make_model']}"),
                  ("Damaged parts found on inspection", ", ".join(sv["damaged_areas"])),
                  ("Garage estimate submitted", rupees(f["estimate"]))]),
        Paragraph("<b>Observations</b>", STYLES["Heading3"]),
        Paragraph(sv["observations"], STYLES["Normal"]),
        Paragraph(f"<b>Opinion:</b> In my opinion the damage <b>{opinion}</b> consistent with the cause of loss "
                  "stated by the insured.", STYLES["Normal"]),
        Paragraph("<b>Assessment of loss</b>", STYLES["Heading3"]),
        table,
        Paragraph("Depreciation and deductible to be applied by the insurer as per policy terms.", STYLES["Italic"]),
    ])


def make_fir(s, folder):
    fir, p = s["fir"], s["policy"]
    write_pdf(folder / "fir.pdf", "First Information Report (FIR) - Copy",
              f"{fir['station']} | FIR No. {fir['fir_number']}", [
        kv_table([("FIR number", fir["fir_number"]), ("Police station", fir["station"]),
                  ("Date of incident", ddmmyyyy(fir["date"])), ("Time of incident", ampm(fir["time"])),
                  ("Place of incident", fir["location"]), ("Vehicle involved", f"{p['vehicle_reg']} ({p['make_model']})"),
                  ("Breath analyser result (driver of insured vehicle)", fir["breath"])]),
        Paragraph("<b>Brief facts</b>", STYLES["Heading3"]),
        Paragraph(fir["summary"], STYLES["Normal"]),
    ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    config.DATA_DIR.mkdir(exist_ok=True)
    # Start fresh every time so the data always matches the scenarios above
    for path in (config.DB_PATH, config.CHECKPOINT_DB_PATH):
        path.unlink(missing_ok=True)
    shutil.rmtree(config.SAMPLE_CLAIMS_DIR, ignore_errors=True)
    config.SAMPLE_CLAIMS_DIR.mkdir(parents=True)
    db.init_db()

    with db.connect() as conn:
        conn.executemany("INSERT INTO garages VALUES (?, ?, ?, ?)", GARAGES)
        for p in [s["policy"] for s in SCENARIOS] + FILLER_POLICIES:
            conn.execute(f"INSERT INTO policies ({', '.join(p)}) VALUES ({', '.join('?' * len(p))})", tuple(p.values()))
        for s in SCENARIOS:
            for claim_date, amount in s["prior_claims"]:
                conn.execute("INSERT INTO prior_claims (policy_number, claim_date, amount) VALUES (?, ?, ?)",
                             (s["policy"]["policy_number"], claim_date, amount))

    label_rows = []
    for s in SCENARIOS:
        folder = config.SAMPLE_CLAIMS_DIR / s["claim_id"]
        folder.mkdir()
        make_claim_form(s, folder)
        make_survey_report(s, folder)
        if s["fir"]:
            make_fir(s, folder)

        route, recommendation, is_fraud = s["expected"]
        # The expected payable is calculated with the SAME rules the agent uses, but from the true
        # line items. So the eval measures: "did the LLM read the survey table correctly?"
        expected_payable = ""
        if recommendation == "APPROVE":
            items = [{"description": d, "category": c, "amount": a} for d, c, a in s["survey"]["items"]]
            expected_payable = rules.assess_amount(items, s["policy"], s["form"]["loss_date"])["payable"]
        label_rows.append({"claim_id": s["claim_id"], "expected_route": route,
                           "expected_recommendation": recommendation, "is_fraud": is_fraud,
                           "expected_payable": expected_payable})
        print(f"  {s['claim_id']}  {len(list(folder.glob('*.pdf')))} PDFs  -> expected {route:<18} "
              f"{recommendation:<8} payable={expected_payable or '-'}")

    with open(config.EVAL_LABELS_PATH, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["claim_id", "expected_route", "expected_recommendation",
                                                "is_fraud", "expected_payable"])
        writer.writeheader()
        writer.writerows(label_rows)

    print(f"\nCreated {config.DB_PATH.name}: {len(SCENARIOS) + len(FILLER_POLICIES)} policies, "
          f"{len(GARAGES)} garages, {sum(len(s['prior_claims']) for s in SCENARIOS)} prior claims")
    print(f"Created {len(SCENARIOS)} sample claims in {config.SAMPLE_CLAIMS_DIR.relative_to(config.ROOT_DIR)}")
    print(f"Created eval labels in {config.EVAL_LABELS_PATH.relative_to(config.ROOT_DIR)}")


if __name__ == "__main__":
    main()
