#!/usr/bin/env python3
"""
County Property Lookup
======================
Searches public ArcGIS REST property/taxlot layers by owner name and/or situs
address.  Supports multiple state counties.  Results include
owner, address, city/state/zip and other parcel attributes plus one-click
Google Maps and Zillow links.

If any changes are made to this main python file it must be rebuilt using the Visual Studio terminal. Simply paste the following commands in and confirm the replace warnings:
source venv/bin/activate && pip install certifi && pyinstaller --noconfirm --windowed --name "DataGrabber" --osx-bundle-identifier "com.datagrabber.DataGrabber" --icon "DataGrabber.icns" --clean --collect-data certifi DataGrabber.py && xattr -cr dist/DataGrabber.app

Or just tell grok "rebuild" and it will throw an updated build into your desktop replacing any old builds that already exist there.
Before the old Desktop app is moved to the Trash, its finished county databases are copied into ~/Library/Application Support/DataGrabber and checked so they stay finished. The running app keeps its databases there, so updates work wherever the .app is stored.



────────────────────────────────────────────────────────────────────────────
HOW TO ADD A COUNTY  (all configuration lives in the single CONFIG dict below)
────────────────────────────────────────────────────────────────────────────

1. Open the CONFIG dictionary (right after this docstring).

2. Under the correct state key (create a new state key if needed) add:

   "Your County Name": {
       # ── Required for every county ──────────────────────────────────────
       "base_url":          "https://…/query",   # ArcGIS query endpoint
       "owner_field":       "OWNER_NAME",        # str  or  list of str
       "address_field":     "SITUS_ADDR",
       "city_field":        "CITY",
       "state_field":       "STATE",             # set to None if layer has none
       "zip_field":         "ZIP",               # set to None if layer has none
       "max_record_count":  2000,                # server page-size limit

       # ── Optional – only include when different from defaults ───────────
       # "object_id_field":      "OBJECTID",     # default is "OBJECTID"
       # "display_address_field": "SitusAddress",# better display field if needed
       # "display_csz_field":    "SitusCSZ",     # rarely needed
       # "supports_expanded":    True,           # auto-fill address from Address Points when situs is empty
       # "address_points_url":   "https://…",    # required when supports_expanded
       # "is_two_step":          True,           # owner layer + details layer pattern
       # "base_url_owners":      "https://…",    # required when is_two_step
       # "base_url_details":     "https://…",    # required when is_two_step
       # "fallback_address_field": "...",        # used only when address_field is empty
       # "fallback_city_field":    "...",        # used only when city_field is empty
       # "fallback_zip_field":     "...",        # used only when zip_field is empty
   },

3. Standard counties need ONLY the required keys.  Copy any existing standard
   county as a template and change the values.

4. Two-step counties (owner list lives in one layer, parcel details in another,
   joined on taxlot id) – example: Deschutes:
   • Set is_two_step = True and supply both base_url_owners / base_url_details.
   • The functions _fetch_deschutes() and build_deschutes_address_where() contain
     Deschutes-specific field names (map_taxlot, house_number, street_name …).
     If a new two-step county uses different field names you must adapt those
     two functions (or add a parallel pair and wire them in fetch_all_results).

5. Expanded-address counties (cleaner address from a separate Address Points
   layer when the taxlot situs address is empty) – example: Jackson:
   • Set supports_expanded = True and supply address_points_url.
   • enrich_expanded_addresses() runs when that county's data file is downloaded.
     It hard-codes Jackson field names (MAPLOT, Taxlot, St_FullAd …).
     Adapt it if you add another county with this pattern.

After you save the new entry the State / County dropdowns update automatically.
The next time the app opens, a county with no finished data file is downloaded
before searches are allowed.  If that county's server is down, the results
window says so and that county cannot be searched.
No other code changes are required for ordinary counties.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import webbrowser
import time
import re
import ctypes
import os
import shutil
import sys
import threading
import json
import sqlite3
import ssl
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, quote_plus, urlencode

# ── SSL certificates for the frozen .app ──────────────────────────────────
# PyInstaller bundles do not always find macOS system CA certs, so every
# HTTPS ArcGIS call fails and searches return zero results.  Prefer certifi
# (include it in the build — see rebuild command in the docstring).
# Fall back to the default context when certifi is unavailable.
def _make_ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL_CONTEXT = _make_ssl_context()


def _urlopen(req, timeout=20):
    """urlopen with an explicit SSL context so HTTPS works inside the .app."""
    return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT)


# ───────────────────────────── Configuration ─────────────────────────────
# Nested by state → county so identical county names across states never collide.
# Adding a new county = one dict entry. Nothing else required for standard cases.
# Only include optional keys when they differ from defaults
# (is_two_step=False, supports_expanded=False, object_id_field="OBJECTID").

CONFIG = {
    "Oregon": {
        "Jackson County": {
            "base_url": "https://jcportal.jacksoncountyor.gov/server/rest/services/Property/Taxlots/MapServer/2/query",
            "owner_field": "FEEOWNER",
            "address_field": "SITEADD",
            "city_field": "CITY",
            "state_field": "STATE",
            "zip_field": "ZIPCODE",
            "max_record_count": 2000,
            "supports_expanded": True,
            "address_points_url": "https://jcportal.jacksoncountyor.gov/server/rest/services/Property/Address_Points/MapServer/1/query",
        },
        "Josephine County": {
            "base_url": "https://gis.co.josephine.or.us/arcgis/rest/services/Assessor/Assessor_Taxlots/FeatureServer/0/query",
            "owner_field": "NAME",
            "address_field": "SITUS",
            "city_field": "SITUS_CITY",
            "state_field": "SITUS_ST",
            "zip_field": "SITUS_ZIP",
            "max_record_count": 2000,
            "fallback_address_field": "ADDRESS",
        },
        "Klamath County": {
            "base_url": "https://services.arcgis.com/H6Mh1bySxR4oHx6x/arcgis/rest/services/KC_Taxlots/FeatureServer/1/query",
            "owner_field": "OWNER_NAME",
            "address_field": "SITUS_ADDRESS",
            "city_field": "SITUSCITY",
            "state_field": "MAILST",
            "zip_field": "SITUSZIP",
            "max_record_count": 1000,
            "fallback_address_field": "OWNER_ADDR1",
            "fallback_city_field": "MAILCITY",
            "fallback_zip_field": "ZIP",
        },
        "Douglas County": {
            "base_url": "https://gis.co.douglas.or.us/server/rest/services/Parcel/Parcels/MapServer/0/query",
            "owner_field": "NAME",
            "address_field": "SitusAddress",
            "city_field": "SitusCSZ",
            "state_field": "SitusCSZ",
            "zip_field": "SitusCSZ",
            "max_record_count": 50000,
        },
        "Crook County": {
            "base_url": "https://gis.crookcountyor.gov/server/rest/services/Public/Crook_County_Taxlots/MapServer/0/query",
            "owner_field": "OWNER_NAME",
            "address_field": "SITUS_ADDRESS",
            "city_field": "M_CITY",
            "state_field": "M_STATE",
            "zip_field": "M_ZIP",
            "max_record_count": 2000,
            "fallback_address_field": "M_ADDRESS",
        },
        "Deschutes County": {
            "owner_field": "name",
            "address_field": "address",
            "city_field": "city",
            "state_field": "state",
            "zip_field": "zip",
            "max_record_count": 2000,
            "is_two_step": True,
            "base_url_owners": "https://maps.deschutes.org/server/rest/services/Hosted/Taxlots/FeatureServer/5/query",
            "base_url_details": "https://maps.deschutes.org/server/rest/services/Hosted/Taxlots/FeatureServer/1/query",
        },
        "Multnomah County": {
            "base_url": "https://services5.arcgis.com/x7DNZL1YqNQVNykA/ArcGIS/rest/services/Multnomah_County_Taxlot_Parcels/FeatureServer/0/query",
            "owner_field": "NAME",
            "address_field": "SITUSADDR",
            "city_field": "SITUSCITY",
            "state_field": "SITUSSTATE",
            "zip_field": "SITUSZIP",
            "max_record_count": 2000,
            "object_id_field": "OBJECTID_1",
        },
        "Lane County": {
            "base_url": "https://lcmaps.lanecounty.org/arcgis/rest/services/EPIL/EPIL/MapServer/1/query",
            "owner_field": "OWNNAME",
            "address_field": "ADDR1",
            "city_field": "OWNERCITY",
            "state_field": "OWNERPRVST",
            "zip_field": "OWNERZIP",
            "max_record_count": 2000,
        },
        "Curry County": {
            "base_url": "https://gateway.maps.rlid.org/maps1/rest/services/CurryInternal/GeneralMap/MapServer/13/query",
            "owner_field": "CurryAssessmentRollExport_Owner_Name",
            "address_field": "CurryAssessmentRollExport_Situs_addr",
            "city_field": "CurryAssessmentRollExport_Situs_City",
            "state_field": "CurryAssessmentRollExport_Situs_State",
            "zip_field": "CurryAssessmentRollExport_Situs_Zip",
            "max_record_count": 1000,
            "fallback_address_field": "CurryAssessmentRollExport_Address_1",
            "fallback_city_field": "CurryAssessmentRollExport_City",
            "fallback_zip_field": "CurryAssessmentRollExport_Zip",
        },
        "Marion County": {
            "base_url": "https://gis.co.marion.or.us/arcgis/rest/services/Public/Parcels/MapServer/0/query",
            "owner_field": "OWNERNAME",
            "address_field": "SITUS",
            "city_field": "SITUSCSZ",
            "state_field": "SITUSCSZ",
            "zip_field": "SITUSCSZ",
            "max_record_count": 1000,
            "fallback_address_field": "OWNERADDR",
        },
        "Polk County": {
            "base_url": "https://maps.co.polk.or.us/gis/rest/services/Assessor/Taxlots/MapServer/11/query",
            "owner_field": "AcctTable_OwnerLine1",
            "address_field": "AcctTable_MailAdd1",
            "city_field": "AcctTable_SiteAddCty",
            "state_field": "AcctTable_MailState",
            "zip_field": "AcctTable_SiteZip",
            "max_record_count": 2000,
            "fallback_address_field": "AcctTable_MailAdd1",
            "fallback_city_field": "AcctTable_MailCity",
            "fallback_zip_field": "AcctTable_MailZip",
        },
        "Benton County": {
            "base_url": "https://gis.co.benton.or.us/arcgis/rest/services/Public/TaxlotOwners/MapServer/0/query",
            "owner_field": "Party_Name",
            "address_field": "Situs_Addr1",
            "city_field": "Situs_City",
            "state_field": "Situs_State",
            "zip_field": "Situs_Zip",
            "max_record_count": 1000,
        },
        "Yamhill County": {
            "base_url": "https://services.arcgis.com/uUvqNMGPm7axC2dD/ArcGIS/rest/services/Yamhill_County_Landowners/FeatureServer/0/query",
            "owner_field": "OWNERLINE1",
            "address_field": "SITEADDNAM",
            "city_field": "SITEADDCTY",
            "state_field": "MAILSTATE",
            "zip_field": "SITEZIP",
            "max_record_count": 2000,
            "object_id_field": "FID",
        },
        "Clatsop County": {
            "base_url": "https://services.arcgis.com/uUvqNMGPm7axC2dD/ArcGIS/rest/services/Clatsop_County_Landowners/FeatureServer/0/query",
            "owner_field": "OWNERLINE1",
            "address_field": "SITEADDNAM",
            "city_field": "SITEADDCTY",
            "state_field": "MAILSTATE",
            "zip_field": "SITEZIP",
            "max_record_count": 2000,
            "fallback_address_field": "MAILADD1",
            "fallback_city_field": "MAILCITY",
            "fallback_zip_field": "MAILZIP",
            "object_id_field": "FID",
        },
        "Harney County": {
            "base_url": "https://services3.arcgis.com/e3KSI9Py4B7m3xu6/ArcGIS/rest/services/02_arcgis_online_Address_and_Tax_Lots/FeatureServer/1/query",
            "owner_field": "OWNER",
            "address_field": "SITUS",
            "city_field": "CITY",
            "state_field": "STATE",
            "zip_field": "ZIP",
            "max_record_count": 2000,
        },
        "Lake County": {
            "base_url": "https://services.arcgis.com/H6Mh1bySxR4oHx6x/ArcGIS/rest/services/LakeCoTaxlots/FeatureServer/0/query",
            "owner_field": "OwnerLine1",
            "address_field": "SiteAddNam",
            "city_field": "SiteAddCty",
            "state_field": "MailState",
            "zip_field": "SiteZip",
            "max_record_count": 1000,
            "fallback_city_field": "MailCity",
            "fallback_zip_field": "MailZip",
        },
        "Malheur County": {
            "base_url": "https://services3.arcgis.com/e3KSI9Py4B7m3xu6/ArcGIS/rest/services/Malheur01TL/FeatureServer/0/query",
            "owner_field": "OWNER",
            "address_field": "ADDRESS2",
            "city_field": "NCITY",
            "state_field": "CITYSTATE",
            "zip_field": "ZIP",
            "max_record_count": 1000,
            "fallback_city_field": "CITYSTATE",
        },
        "Coos County": {
            "base_url": "https://services.arcgis.com/uUvqNMGPm7axC2dD/ArcGIS/rest/services/Coos_County_Forestland_Classification_Taxlots_2/FeatureServer/0/query",
            "owner_field": "OWNER1",
            "address_field": "SITUSSTR",
            "city_field": "SITUSCITY",
            "state_field": "MAILST",
            "zip_field": "SITUSZIP",
            "max_record_count": 2000,
            "fallback_address_field": "MAIL1",
            "fallback_city_field": "MAILCITY",
            "fallback_zip_field": "ZIP",
        },
        "Wheeler County": {
            "base_url": "https://services3.arcgis.com/e3KSI9Py4B7m3xu6/ArcGIS/rest/services/Wheeler01_TL/FeatureServer/0/query",
            "owner_field": "OWNER",
            "address_field": "M_ADDRESS",
            "city_field": "M_CITY",
            "state_field": "M_STATE",
            "zip_field": "M_Zip",
            "max_record_count": 1000,
        },
        "Tillamook County": {
            "base_url": "https://services.arcgis.com/uUvqNMGPm7axC2dD/ArcGIS/rest/services/Tillamook_County_Landowners/FeatureServer/0/query",
            "owner_field": "OWNERLINE1",
            "address_field": "SITEADDNAM",
            "city_field": "SITEADDCTY",
            "state_field": "MAILSTATE",
            "zip_field": "SITEZIP",
            "max_record_count": 2000,
            "fallback_address_field": "MAILADD1",
            "fallback_city_field": "MAILCITY",
            "fallback_zip_field": "MAILZIP",
            "object_id_field": "FID",
        },
        "Wasco County": {
            "base_url": "https://public.co.wasco.or.us/gisserver/rest/services/Taxlots/MapServer/0/query",
            "owner_field": "Taxpayer",
            "address_field": "MailingAddress1",
            "city_field": "MailingCity",
            "state_field": "MailingState",
            "zip_field": "MailingZIP",
            "max_record_count": 100,
        },
        "Gilliam County": {
            "base_url": "https://services3.arcgis.com/e3KSI9Py4B7m3xu6/ArcGIS/rest/services/Gilliam01_TL/FeatureServer/6/query",
            "owner_field": "OWNERSHIP",
            "address_field": "SITUS",
            "city_field": "CITY",
            "state_field": "STATE",
            "zip_field": "ZIP",
            "max_record_count": 1000,
            "fallback_address_field": "M_ADDRESS",
            "fallback_city_field": "M_CITY",
            "fallback_zip_field": "M_ZIP",
        },
        "Morrow County": {
            "base_url": "https://services.arcgis.com/uUvqNMGPm7axC2dD/ArcGIS/rest/services/South_Heppner_Elk_Home_Ranges_WFL1/FeatureServer/179/query",
            "owner_field": "OWNERLINE1",
            "address_field": "SITEADDNAM",
            "city_field": "SITEADDCTY",
            "state_field": "MAILSTATE",
            "zip_field": "SITEZIP",
            "max_record_count": 2000,
            "fallback_city_field": "MAILCITY",
        },
        "Umatilla County": {
            "base_url": "https://services3.arcgis.com/tNPgIZWOB0Efvm0g/ArcGIS/rest/services/Tax_Lots/FeatureServer/0/query",
            "owner_field": "MAILING_NA",
            "address_field": "M_ADDRESS",
            "city_field": "M_CITY",
            "state_field": "M_STATE",
            "zip_field": "zip",
            "max_record_count": 2000,
            "object_id_field": "FID",
        },
        "Hood River County": {
            "base_url": "https://services7.arcgis.com/xtfQPw7zwSRPkcfN/arcgis/rest/services/Historical_Taxlots_2023/FeatureServer/9/query",
            "owner_field": "OWNER",
            "address_field": "SITUS",
            "city_field": "CITY",
            "state_field": "STATE",
            "zip_field": "ZIP",
            "max_record_count": 2000,
        },
        "Washington County": {
            "base_url": "https://services.arcgis.com/uUvqNMGPm7axC2dD/ArcGIS/rest/services/Washington_County_Landowners/FeatureServer/0/query",
            "owner_field": "OWNERNAME",
            "address_field": "SITEADDR",
            "city_field": "OWNERCITY",
            "state_field": "OWNERSTATE",
            "zip_field": "OWNERZIP",
            "max_record_count": 2000,
            "fallback_address_field": "OWNERADDR1",
            "object_id_field": "FID",
        },
        "Columbia County": {
            "base_url": "https://services.arcgis.com/uUvqNMGPm7axC2dD/ArcGIS/rest/services/Columbia_County_Landowners/FeatureServer/0/query",
            "owner_field": "OWNERLINE1",
            "address_field": "SITEADDNAM",
            "city_field": "SITEADDCTY",
            "state_field": "MAILSTATE",
            "zip_field": "SITEZIP",
            "max_record_count": 2000,
            "fallback_address_field": "MAILADD1",
            "fallback_city_field": "MAILCITY",
            "fallback_zip_field": "MAILZIP",
            "object_id_field": "FID",
        },
    },
    "California": {
        "Amador County": {
            "base_url": "https://services8.arcgis.com/uzb563eo87NppqyM/arcgis/rest/services/Amador_County_Parcels/FeatureServer/0/query",
            "owner_field": "Owner_1",
            "address_field": "SITUS1",
            "city_field": "ADDRCITY",
            "state_field": "ADDRSTATE",
            "zip_field": "ADDRZIP",
            "max_record_count": 2000,
            "object_id_field": "FID",
        },
        "Calaveras County": {
            "base_url": "https://services.arcgis.com/0xnwbwUttaTjns4i/arcgis/rest/services/Parcels_Calaveras_County/FeatureServer/0/query",
            "owner_field": "NAME",
            "address_field": "SITUS1",
            "city_field": "ADDRESS2",
            "state_field": "SITUS2",
            "zip_field": "ADDRESS2",
            "max_record_count": 2000,
            "fallback_address_field": "ADDRESS1",
            "object_id_field": "FID",
        },
        "Mariposa County": {
            "base_url": "https://services2.arcgis.com/wEula7SYiezXcdRv/ArcGIS/rest/services/Mariposa_County_GIS_TylerTech_FEATURE_SERVICE/FeatureServer/0/query",
            "owner_field": "OwnerFull",
            "address_field": "AddressStreet",
            "city_field": "AddressCity",
            "state_field": "AddressState",
            "zip_field": "AddressZip",
            "max_record_count": 2000,
        },
        "Riverside County": {
            "base_url": "https://services3.arcgis.com/5H4ySekQnCDr5Bzc/arcgis/rest/services/ParcelViewer_EA/FeatureServer/0/query",
            "owner_field": "OWNER1_LAST_NAME",
            "address_field": "MAIL_TO_STREET",
            "city_field": "MAIL_TO_CITY",
            "state_field": "MAIL_TO_CITY",
            "zip_field": "MAIL_TO_ZIP",
            "max_record_count": 1000,
        },
        "San Benito County": {
            "base_url": "https://services2.arcgis.com/NjMFCzThTMQy3AJa/arcgis/rest/services/ParcelsTyler2/FeatureServer/2/query",
            "owner_field": "Owner",
            "address_field": "Situs1",
            "city_field": "Situs2",
            "state_field": "Situs2",
            "zip_field": "Situs2",
            "max_record_count": 1000,
            "object_id_field": "FID",
        },
        "San Diego County": {
            "base_url": "https://webmaps.sandiego.gov/arcgis/rest/services/GeocoderMerged/MapServer/1/query",
            "owner_field": "OWN_ADDR1",
            "address_field": "SITUS_STREET",
            "city_field": "SITUS_COMMUNITY",
            "state_field": "SITUS_COMMUNITY",
            "zip_field": "SITUS_ZIP",
            "max_record_count": 2000,
            "fallback_address_field": "SITUS_STREET",
        },
        "Mendocino County": {
            "base_url": "https://services.arcgis.com/0xnwbwUttaTjns4i/ArcGIS/rest/services/MCRCD_Data/FeatureServer/0/query",
            "owner_field": "OWNER",
            "address_field": "SITUS_ADD",
            "city_field": "SITUS_CTY",
            "state_field": "STATE",
            "zip_field": "SITUS_ZIP",
            "max_record_count": 2000,
            "fallback_address_field": "ADDRESS",
            "fallback_city_field": "CITY",
            "fallback_zip_field": "ZIP",
            "object_id_field": "FID",
        },
    },
    "Florida": {
        "Palm Beach County": {
            "base_url": "https://gis.pbcgov.org/arcgis/rest/services/Parcels/PARCEL_INFO/MapServer/4/query",
            "owner_field": "OWNER_NAME1",
            "address_field": "SITE_ADDR_STR",
            "city_field": "CITYNAME",
            "state_field": "STATE",
            "zip_field": "ZIP1",
            "max_record_count": 5000,
            "fallback_address_field": "PADDR1",
            "fallback_city_field": "CITYNAME",
            "object_id_field": "OBJECTID_1",
        },
        "Volusia County": {
            "base_url": "https://maps5.vcgov.org/arcgis/rest/services/Basemap/MapServer/6/query",
            "owner_field": ["OWNER1", "OWNER2"],
            "address_field": "SITUS_ADDR",
            "city_field": "CITY",
            "state_field": "STATE",
            "zip_field": "ZIP",
            "max_record_count": 1000,
        },
    },
}

# Counties that could not be added (no usable public ArcGIS layer found, or
# data was too incomplete / gated).  Reference only — not used by the program.
unavailableCounties = {
    "Oregon": [
        "Grant County",
        "Baker County",
        "Clackamas County",
        "Jefferson County",
        "Sherman County",
        "Union County",
        "Wallowa County",
        "Lincoln County",
        "Linn County",
    ],
    "California": [
        "Alameda County",
        "Alpine County",
        "Colusa County",
        "Contra Costa County",
        "Del Norte County",
        "El Dorado County",
        "Fresno County",
        "Glenn County",
        "Humboldt County",
        "Imperial County",
        "Inyo County",
        "Kern County",
        "Kings County",
        "Lake County",
        "Los Angeles County",
        "Madera County",
        "Marin County",
        "Merced County",
        "Modoc County",
        "Mono County",
        "Monterey County",
        "Napa County",
        "Nevada County",
        "Orange County",
        "Placer County",
        "Plumas County",
        "Sacramento County",
        "San Bernardino County",
        "San Francisco County",
        "San Joaquin County",
        "San Luis Obispo County",
        "San Mateo County",
        "Santa Barbara County",
        "Santa Clara County",
        "Santa Cruz County",
        "Shasta County",
        "Sierra County",
        "Siskiyou County",
        "Solano County",
        "Sonoma County",
        "Stanislaus County",
        "Sutter County",
        "Tehama County",
        "Trinity County",
        "Tulare County",
        "Tuolumne County",
        "Ventura County",
        "Yolo County",
        "Yuba County",
        "Butte County",
        "Lassen County",
    ],
    "Florida": [
        # add any unavailable counties here
    ],
}

# Official total county count per state.  Used by the Statewide "Display County
# Search Links" header to show progress (implemented = CONFIG + unavailable).
maxCountyCount = {
    "Oregon": 36,
    "California": 58,
    "Florida": 67,
}

COUNTRYWIDE = "Countrywide"
MAP_LINK_GOOGLE_MAPS = "Google Maps"
MAP_LINK_GOOGLE_EARTH = "Google Earth"
MAP_LINK_APPLE_MAPS = "Apple Maps"
MAP_LINK_TYPES = (
    MAP_LINK_GOOGLE_MAPS,
    MAP_LINK_GOOGLE_EARTH,
    MAP_LINK_APPLE_MAPS,
)

# ───────────────────────────── Global state ─────────────────────────────
current_search_name = ""
current_search_county = ""
all_matching_taxlots = []
taxlot_to_owner_map = {}
displayed_results = 0
total_results = 0
BATCH_SIZE = 10
RESULT_DISPLAY_CAP = 10000


def overflow_warning_line(total=None):
    """Top-of-results line when the search matched more than the display cap."""
    if total is None or total <= RESULT_DISPLAY_CAP:
        count = "more than 10000"
    else:
        count = str(int(total))
    return (
        f"WARNING: There are {count} results in your search and will stop "
        "displaying results at 10000. Narrow your search to get fewer results."
    )
is_loading = False
blink_job = None
_search_id = 0  # incremented on every new search; old threads check this and abort
_shown_state = ""
_shown_county = ""
_open_choice = None
_holding_download_status = False
searches_allowed = True
session_failed_counties = set()  # (state, county) whose refresh failed this launch
_refresh_started = False  # one background refresh per launch
_session_refresh_claimed = set()  # counties this launch already queued
schedule = []  # [(state, county), ...] oldest current file first; rebuilt each launch
_pinned_result_notes = []  # lines kept at the top of the results window
_user_scrolled = False
UNINCORPORATED_CITY = "Unincorporated"
_US_STATES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN", "MISSISSIPPI": "MS",
    "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE", "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ", "NEW MEXICO": "NM", "NEW YORK": "NY",
    "NORTH CAROLINA": "NC", "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK",
    "OREGON": "OR", "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA", "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI", "WYOMING": "WY", "DISTRICT OF COLUMBIA": "DC",
}
_US_ABBREVS = set(_US_STATES.values())
_county_line_job = None
_county_line_token = 0
_gold_blinking = False
_gold_blink_job = None
_gold_bright = False
_finish_seconds_left = 0
_finish_timer = None
_feature_cache = {}
_feature_cache_order = []
_session_started = datetime.now()
_polite_download = False
_user_busy = threading.Event()
_user_busy_count = 0
_user_busy_threads = set()
_user_busy_lock = threading.Lock()
_counties_download_failed = set()
_auto_update_on = threading.Event()  # checkbox; off until a saved "on" is read
_refresh_active = False  # the one refresh thread is inside its county list
_refreshing_county = None  # county the gold line is grabbing an update for
_startup_ready = False  # county folders have been cleaned for this launch


def _set_thread_qos(background):
    """Background refresh stays below clicks and searches on macOS."""
    if sys.platform != "darwin":
        return
    try:
        libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        # QOS_CLASS_UTILITY is below QOS_CLASS_USER_INITIATED.
        qos = 0x11 if background else 0x19
        libsystem.pthread_set_qos_class_self_np(ctypes.c_uint(qos), ctypes.c_int(0))
    except Exception:
        return


def _user_work_enter():
    global _user_busy_count
    with _user_busy_lock:
        _user_busy_count += 1
        _user_busy_threads.add(threading.get_ident())
        _user_busy.set()


def _user_work_leave():
    global _user_busy_count
    with _user_busy_lock:
        _user_busy_count -= 1
        _user_busy_threads.discard(threading.get_ident())
        if _user_busy_count <= 0:
            _user_busy_count = 0
            _user_busy_threads.clear()
            _user_busy.clear()


def _release_user_work(release):
    """Drop one search's busy flag. A second call does nothing."""
    if not release or not release.get("open"):
        return
    release["open"] = False
    _user_work_leave()


def _wait_for_auto_update():
    """Stay on the current refresh step while the auto-update checkbox is off.

    The same download thread is waiting, so turning the box back on in this
    session continues that county instead of starting its file over.
    """
    while not _auto_update_on.is_set():
        _auto_update_on.wait(timeout=0.2)


def _polite_pause():
    """Let a click or a search run. A first-time county download does not pause.

    A background refresh also waits here while auto-update is turned off.
    """
    if not _polite_download:
        return
    with _user_busy_lock:
        if threading.get_ident() in _user_busy_threads:
            return
    while _user_busy.is_set():
        with _user_busy_lock:
            if threading.get_ident() in _user_busy_threads:
                return
        if not _auto_update_on.is_set():
            _auto_update_on.wait(timeout=0.05)
            continue
        time.sleep(0.05)
    _wait_for_auto_update()
    time.sleep(0.001)

# ───────────────────────────── Helpers ─────────────────────────────
def _count_noun(n, singular, plural=None):
    if plural is None:
        plural = singular + "s"
    return singular if n == 1 else plural


def get_cfg():
    state = state_var.get()
    county = county_var.get()
    if state == COUNTRYWIDE or county == "Statewide" or not county:
        return None
    return CONFIG[state][county]


def get_current_county():
    return county_var.get()


def update_county_dropdown(*_):
    global _shown_state, _shown_county
    selected = state_var.get()
    # Opening the state list and clicking off writes the same state again.
    # That must not clear a search or zero its result total.
    if selected == _shown_state:
        return
    _shown_state = selected
    if selected == COUNTRYWIDE:
        county_dropdown.close()
        county_dropdown.set_values([])
        _shown_county = None
        if county_var.get() == "":
            on_county_change()
        else:
            county_var.set("")
        return
    counties = list(CONFIG[selected].keys()) + ["Statewide"]
    county_dropdown.set_values(counties)
    first = counties[0]
    # The new state's first county can share a name with the old selection.
    _shown_county = None
    if county_var.get() == first:
        on_county_change()
    else:
        county_var.set(first)


def on_county_change(*_):
    global _search_id, _shown_county
    county = county_var.get()
    if county == _shown_county:
        return
    _shown_county = county
    # Cancel any in-flight search so its results cannot appear after a switch
    _search_id += 1
    stop_loading()
    reset_to_search_mode()
    clear_output()
    clear_entry_selections()
    root.after(20, position_loading)
    note_offline_selection()


def build_where_clause(name_input, address_input, cfg):
    """Build ArcGIS WHERE. Uses UPPER() for case-insensitive matching."""
    conditions = []
    name = " ".join(name_input.strip().upper().split()) if name_input else ""
    address = " ".join(address_input.strip().upper().split()) if address_input else ""

    if name:
        owner_fields = cfg.get("owner_field", "NAME")
        if not isinstance(owner_fields, list):
            owner_fields = [owner_fields]
        seen = set()
        for word in re.split(r"\s+", name):
            word = word.strip(",.")
            if not word or len(word) == 1 or word in seen:
                continue
            seen.add(word)
            safe = word.replace("'", "''")
            conditions.append(
                "(" + " OR ".join(f"UPPER({f}) LIKE '%{safe}%'" for f in owner_fields) + ")"
            )

    if address and not cfg.get("is_two_step"):
        addr_field = cfg["address_field"]
        parts = address.split()
        if parts and parts[0].isdigit():
            hn = parts[0].replace("'", "''")
            conditions.append(f"UPPER({addr_field}) LIKE '{hn} %'")
            for part in parts[1:]:
                part = part.replace("'", "''")
                if part:
                    conditions.append(f"UPPER({addr_field}) LIKE '%{part}%'")
        else:
            for part in parts:
                part = part.replace("'", "''")
                if part:
                    conditions.append(f"UPPER({addr_field}) LIKE '%{part}%'")

    return " AND ".join(conditions) if conditions else "1=1"


# ── Special handlers (only touch these when adding non-standard counties) ──

def build_deschutes_address_where(address_input):
    """Deschutes-specific address WHERE (uses house_number / street_name / street_type)."""
    if not address_input or not address_input.strip():
        return "1=1"
    addr = " ".join(address_input.strip().upper().split())
    parts = addr.split()
    conditions = []
    house_num = None
    remaining = parts[:]
    if parts and parts[0].isdigit():
        house_num = parts[0]
        remaining = parts[1:]

    common_types = {"ST", "AVE", "RD", "DR", "LN", "CT", "WAY", "BLVD", "PL", "TER", "CIR", "LOOP", "PKWY", "HWY"}
    street_type = remaining[-1] if remaining and remaining[-1] in common_types else None
    street_name_parts = remaining[:-1] if street_type else remaining
    street_name = " ".join(street_name_parts) if street_name_parts else None

    if house_num:
        conditions.append(f"house_number = '{house_num.replace(chr(39), chr(39)*2)}'")
    if street_name:
        conditions.append(f"street_name LIKE '%{street_name.replace(chr(39), chr(39)*2)}%'")
    if street_type:
        conditions.append(f"street_type = '{street_type}'")
    if not conditions:
        conditions.append(f"address LIKE '%{addr.replace(chr(39), chr(39)*2)}%'")
    return " AND ".join(conditions)


def _parse_arcgis_json(raw_bytes):
    """Parse ArcGIS JSON. strict=False tolerates control characters that some
    county servers (e.g. San Diego) embed in text fields — otherwise json.loads
    raises and the search appears to return zero results."""
    text = raw_bytes.decode("utf-8", errors="replace")
    return json.loads(text, strict=False)


def fetch_paginated(base_url, where, page_size=2000, object_id_field="OBJECTID", out_fields="*"):
    features = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "false",
            "f": "pjson",
            "resultOffset": offset,
            "resultRecordCount": page_size,
            "orderByFields": f"{object_id_field} ASC",
        }
        # quote (not quote_plus) so spaces are %20 — some ArcGIS hosts reject "+"
        query = urlencode(params, quote_via=quote)
        full_url = f"{base_url}?{query}"
        req = urllib.request.Request(full_url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with _urlopen(req, timeout=20) as response:
                data = _parse_arcgis_json(response.read())
            if data.get("error"):
                break
            batch = data.get("features", [])
            features.extend(batch)
            _polite_pause()
            if not data.get("exceededTransferLimit", False):
                break
            offset += page_size
        except Exception as e:
            # Remember last failure so the UI can show a real error instead of
            # a silent "No matching properties found." (common when SSL certs
            # are missing inside a frozen .app).
            fetch_paginated.last_error = str(e)
            break
    return features

fetch_paginated.last_error = None


def _fetch_deschutes(name_input, address_input, cfg):
    """Two-step fetcher used when is_two_step=True (Deschutes field names)."""
    taxlot_to_owner = {}
    owner_taxlots = set()
    address_taxlots = set()
    name_input = (name_input or "").strip()
    address_input = (address_input or "").strip()
    page = cfg.get("max_record_count", 2000)

    if name_input:
        where = build_where_clause(name_input, "", cfg)
        owners = fetch_paginated(cfg["base_url_owners"], where, page, "objectid")
        for o in owners:
            attrs = o.get("attributes", {})
            tl = attrs.get("map_taxlot")
            nm = attrs.get("name")
            if tl and nm:
                if "," in nm and ", " not in nm:
                    nm = nm.replace(",", ", ")
                taxlot_to_owner[tl] = nm
                owner_taxlots.add(tl)

    if address_input:
        where = build_deschutes_address_where(address_input)
        feats = fetch_paginated(cfg["base_url_details"], where, page, "objectid", "taxlot")
        for f in feats:
            tl = f.get("attributes", {}).get("taxlot")
            if tl:
                address_taxlots.add(str(tl).strip())

    if name_input and address_input:
        final = list(owner_taxlots & address_taxlots)
    elif name_input:
        final = list(owner_taxlots)
    elif address_input:
        final = list(address_taxlots)
        # fill owners for address-only hits
        for i in range(0, len(final), 400):
            chunk = final[i:i + 400]
            safe = [str(t).replace("'", "''") for t in chunk]
            where = f"map_taxlot IN ({','.join(repr(t) for t in safe)})"
            try:
                owners = fetch_paginated(cfg["base_url_owners"], where, len(chunk) + 10, "objectid", "map_taxlot,name")
                for o in owners:
                    attrs = o.get("attributes", {})
                    tl, nm = attrs.get("map_taxlot"), attrs.get("name")
                    if tl and nm:
                        if "," in nm and ", " not in nm:
                            nm = nm.replace(",", ", ")
                        taxlot_to_owner[tl] = nm
            except Exception as e:
                root.after(0, lambda err=e: append_output(f"Warning: owner lookup failed for some taxlots: {err}\n"))
    else:
        final = []

    return (final, taxlot_to_owner) if final else ([], {})


def enrich_expanded_addresses(features, cfg):
    """For supports_expanded counties (e.g. Jackson): when the primary situs
    address is empty, look up the Address Points layer by taxlot and fill
    EXPANDED_* fields.  Display prefers those over any mailing-address fallback.
    Field names are Jackson-specific; adapt if adding another expanded county.
    """
    if not features or not cfg or not cfg.get("supports_expanded"):
        return features
    points_url = cfg.get("address_points_url")
    if not points_url:
        return features

    addr_field = cfg.get("address_field")
    # Collect taxlots that need an address fill-in
    need_maplots = []
    seen = set()
    for f in features:
        attrs = f.get("attributes", {})
        raw = attrs.get(addr_field)
        if raw is not None and str(raw).strip():
            continue
        maplot = attrs.get("MAPLOT")
        if maplot is None:
            continue
        key = str(maplot).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        need_maplots.append(key)

    if not need_maplots:
        return features

    address_map = {}
    chunk_size = 50
    for i in range(0, len(need_maplots), chunk_size):
        chunk = need_maplots[i:i + chunk_size]
        where = " OR ".join(
            f"Taxlot = '{m.replace(chr(39), chr(39) + chr(39))}'" for m in chunk
        )
        try:
            pts = fetch_paginated(points_url, where, object_id_field="OBJECTID")
        except Exception:
            pts = []
        for p in pts:
            a = p.get("attributes", {})
            taxlot = a.get("Taxlot")
            if taxlot is None:
                continue
            key = str(taxlot).strip()
            if not key:
                continue
            address_map[key] = {
                "address": a.get("St_FullAd"),
                "city": a.get("Post_Comm"),
                "state": a.get("State"),
                "zip": a.get("Post_Code"),
            }

    for f in features:
        attrs = f.get("attributes", {})
        maplot = attrs.get("MAPLOT")
        if maplot is None:
            continue
        key = str(maplot).strip()
        addr = address_map.get(key)
        if not addr:
            continue
        attrs["EXPANDED_ADDRESS"] = addr["address"]
        attrs["EXPANDED_CITY"] = addr["city"]
        attrs["EXPANDED_STATE"] = addr["state"]
        attrs["EXPANDED_ZIP"] = addr["zip"]

    return features


# ───────────────────────────── Local taxlot files ─────────────────────────────
# The frozen app stores county files in Application Support, not inside the
# .app. Writing the bundle makes macOS ask for permission on every update, and
# it breaks when the app is moved. A source run keeps the files beside this
# script. Each county is one SQLite file. meta.updated is "YYYY-MM-DD HH:MM:SS"
# and meta.finished is "0" until every parcel is committed, then "1". A file
# left unfinished is deleted on the next launch. Searches read matching rows
# only. Older .py JSON files are converted once, not re-downloaded.

def _bundle_resources_dir():
    """Contents/Resources of the running .app, wherever that app is stored."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent.parent / "Resources"


def _app_support_dir():
    return Path.home() / "Library" / "Application Support" / "DataGrabber"


def _resources_dir():
    if getattr(sys, "frozen", False):
        return _app_support_dir()
    return Path(__file__).resolve().parent


def _is_permission_error(exc):
    return isinstance(exc, PermissionError) or getattr(exc, "errno", None) in (1, 13)


def _protected_folder_name(path):
    """Desktop, Documents, or Downloads when path is inside one of those."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return None
    home = Path.home()
    for name in ("Desktop", "Documents", "Downloads"):
        try:
            resolved.relative_to(home / name)
            return name
        except ValueError:
            continue
    return None


def _copy_data_file(src, dst):
    """Copy file bytes only.

    copy2 also copies the macOS permission tags, and those tags make the
    system ask again for every county that gets replaced.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".replacing")
    try:
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


def _finished_stamp(path):
    path = Path(path)
    if not path.is_file():
        return None
    if path.suffix == ".sqlite":
        header = peek_county_header(path)
    elif path.suffix == ".py":
        header = _peek_py_header(path)
    else:
        return None
    if not header or not header[1]:
        return None
    return header[0]


def migrate_bundled_taxlots(bundle_resources, support_dir):
    """Copy finished county files out of a .app into Application Support.

    A newer finished file already in Application Support stays there.
    """
    src_dir = Path(bundle_resources) / "taxlots_current"
    if not src_dir.is_dir():
        return 0
    dst_dir = Path(support_dir) / "taxlots_current"
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in src_dir.iterdir():
        if not src.is_file() or src.suffix not in {".sqlite", ".py"}:
            continue
        src_stamp = _finished_stamp(src)
        if src_stamp is None:
            continue
        dst = dst_dir / src.name
        dst_stamp = _finished_stamp(dst)
        if dst_stamp is not None and dst_stamp >= src_stamp:
            continue
        _copy_data_file(src, dst)
        copied += 1
    return copied


def _ask_for_saved_permission(path):
    """Send the user to the remembered privacy switch. The system dialog will
    not appear again after they choose Don't Allow."""
    kind = _protected_folder_name(path)
    where = f"your {kind} folder" if kind else "its county files"
    try:
        from tkinter import messagebox
        messagebox.showinfo(
            "DataGrabber",
            f"DataGrabber needs access to {where}. "
            "Turn DataGrabber on under Privacy & Security in System Settings. "
            "macOS saves that choice, so it will not ask on every update. "
            "Then open DataGrabber again.",
        )
    except Exception:
        pass
    for url in (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_FilesAndFolders",
        "x-apple.systempreferences:com.apple.preference.security?Privacy",
    ):
        try:
            subprocess.run(["open", url], check=False)
            break
        except OSError:
            continue


def prepare_storage_access():
    """Check the folders this launch will use, and ask once if macOS blocks them.

    Updates write Application Support, which does not depend on where the .app
    sits. If an older copy of the app still has county files inside it, reading
    that folder is what shows the one system permission dialog.
    """
    support = _app_support_dir() if getattr(sys, "frozen", False) else _resources_dir()
    try:
        support.mkdir(parents=True, exist_ok=True)
        probe = support / ".write_ok"
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as exc:
        if _is_permission_error(exc):
            _ask_for_saved_permission(support)
            return False
        return False
    bundle = _bundle_resources_dir()
    if bundle is None:
        return True
    bundled = bundle / "taxlots_current"
    if not bundled.exists():
        return True
    try:
        os.listdir(bundled)
    except OSError as exc:
        if _is_permission_error(exc):
            _ask_for_saved_permission(bundled)
            return False
        return False
    return True


def current_data_dir():
    return _resources_dir() / "taxlots_current"


def update_data_dir():
    return _resources_dir() / "taxlots_update"


def _name_token(label):
    """oregon-jackson, oregon-newYork.  Drop a trailing 'County' word."""
    text = re.sub(r"\s+County$", "", (label or "").strip())
    parts = [p for p in re.split(r"\s+", text) if p]
    if not parts:
        return "unknown"
    token = parts[0].lower()
    for part in parts[1:]:
        token += part[:1].upper() + part[1:].lower()
    return token


def county_file_name(state, county):
    return f"{_name_token(state)}-{_name_token(county)}.sqlite"


def county_file_path(directory, state, county):
    return Path(directory) / county_file_name(state, county)


def iter_implemented_counties():
    for state, counties in CONFIG.items():
        for county in counties:
            yield state, county


_UPDATED_RE = re.compile(r'^updated = "(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"\s*$')
_FINISHED_RE = re.compile(r"^finished = (True|False)\s*$")


def _header_text(updated, finished_true):
    # "True " and "False" are the same length so the header can be rewritten in place.
    token = "True " if finished_true else "False"
    return f'updated = "{updated}"\nfinished = {token}\n'


def _peek_py_header(path):
    """Read the old text header. Return (updated_stamp, finished) or None."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            updated_line = handle.readline()
            finished_line = handle.readline()
    except OSError:
        return None
    updated_match = _UPDATED_RE.match(updated_line.rstrip("\n"))
    finished_match = _FINISHED_RE.match(finished_line.rstrip("\n"))
    if not updated_match or not finished_match:
        return None
    return updated_match.group(1), finished_match.group(1) == "True"


def _sqlite_ro_uri(path):
    return "file:" + quote(Path(path).resolve().as_posix(), safe="/") + "?mode=ro"


def _connect_county_db(path, *, readonly=False, synchronous="FULL"):
    path = Path(path)
    if readonly:
        con = sqlite3.connect(_sqlite_ro_uri(path), uri=True, timeout=30)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        if synchronous not in {"FULL", "NORMAL", "OFF"}:
            synchronous = "FULL"
        con = sqlite3.connect(path, timeout=30)
        con.execute(f"PRAGMA synchronous={synchronous}")
        con.execute("PRAGMA journal_mode=DELETE")
        # Index builds spill to disk so a multi-gigabyte county does not stall the machine.
        con.execute("PRAGMA temp_store=FILE")
    return con


def peek_county_header(path):
    """Return (updated_stamp, finished) or None if the file is unreadable."""
    path = Path(path)
    if path.suffix == ".py":
        return _peek_py_header(path)
    try:
        con = _connect_county_db(path, readonly=True)
    except sqlite3.Error:
        return None
    try:
        try:
            updated = con.execute("SELECT value FROM meta WHERE key='updated'").fetchone()
            finished = con.execute("SELECT value FROM meta WHERE key='finished'").fetchone()
        except sqlite3.Error:
            return None
    finally:
        con.close()
    if not updated or not finished:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", updated[0]):
        return None
    if finished[0] not in ("0", "1"):
        return None
    return updated[0], finished[0] == "1"


def format_updated_for_user(stamp):
    moment = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    hour = str(moment.hour % 12 or 12)
    ampm = "AM" if moment.hour < 12 else "PM"
    return f"{moment.strftime('%B')} {moment.day}, {moment.year} {hour}:{moment.strftime('%M')} {ampm}"


def _now_stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def mark_county_header(path, finished_true, updated=None):
    header = peek_county_header(path)
    if header is None:
        raise CountyDownloadError(f"Cannot read {Path(path).name}")
    current_updated, _current_finished = header
    if updated is None:
        updated = current_updated
    con = _connect_county_db(path)
    try:
        con.execute("UPDATE meta SET value=? WHERE key='finished'", ("1" if finished_true else "0",))
        con.execute("UPDATE meta SET value=? WHERE key='updated'", (updated,))
        con.commit()
    finally:
        con.close()


def _fsync(handle):
    handle.flush()
    os.fsync(handle.fileno())


def _fsync_dir(directory):
    """Keep the new file name itself if the app is closed right after creating it."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


_COUNTY_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parcels (
    id INTEGER PRIMARY KEY,
    owner TEXT NOT NULL,
    address TEXT NOT NULL,
    house_number TEXT NOT NULL,
    street_name TEXT NOT NULL,
    street_type TEXT NOT NULL,
    attributes TEXT NOT NULL
);
"""


def _parcel_columns(feature, cfg):
    """Search columns plus the full attribute record the results window prints."""
    attrs = feature.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}
    owner_fields = cfg.get("owner_field", "NAME")
    if not isinstance(owner_fields, list):
        owner_fields = [owner_fields]
    owner = "\n".join(_field_text(attrs, field).upper() for field in owner_fields)
    raw_address = _field_text(attrs, cfg.get("address_field"))
    if cfg.get("is_two_step"):
        house = attrs.get("house_number")
        house_number = "" if house is None else str(house).strip()
        address = raw_address
        street_name = _field_text(attrs, "street_name")
        street_type = _field_text(attrs, "street_type")
    else:
        house_number = ""
        address = raw_address.upper()
        street_name = ""
        street_type = ""
    payload = json.dumps(attrs, ensure_ascii=False, separators=(",", ":"))
    return owner, address, house_number, street_name, street_type, payload


def write_feature_batches(path, batches, cfg, finished_stamp=None, synchronous="FULL"):
    """Write parcels with finished=0, then set finished=1 only after the last row.

    The unfinished database is committed before the first record is requested, so
    closing the app mid-download leaves finished = 0 on disk.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    started = finished_stamp or _now_stamp()
    con = _connect_county_db(path, synchronous=synchronous)
    try:
        con.executescript(_COUNTY_SCHEMA)
        con.execute(
            "INSERT INTO meta(key, value) VALUES ('updated', ?), ('finished', '0')",
            (started,),
        )
        con.commit()
        _fsync_dir(path.parent)
        pending = []
        written = 0

        def flush():
            nonlocal pending
            if not pending:
                return
            con.executemany(
                "INSERT INTO parcels(owner, address, house_number, street_name, street_type, attributes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                pending,
            )
            con.commit()
            pending = []

        for batch in batches:
            _polite_pause()
            for feature in batch:
                pending.append(_parcel_columns(feature, cfg))
                written += 1
                if len(pending) >= 1000:
                    flush()
                if written % 200 == 0:
                    _polite_pause()
        flush()
        if written == 0:
            raise CountyDownloadError("The county database returned no records")
        # Bulk inserts may use NORMAL. The finished flag is committed in FULL
        # so a completed file is still on disk after a power loss.
        con.execute("PRAGMA synchronous=FULL")
        con.execute("CREATE INDEX IF NOT EXISTS idx_address ON parcels(address)")
        done = finished_stamp or _now_stamp()
        con.execute("UPDATE meta SET value='1' WHERE key='finished'")
        con.execute("UPDATE meta SET value=? WHERE key='updated'", (done,))
        con.commit()
        return written
    finally:
        con.close()


def iter_legacy_features(path):
    """Stream feature objects out of an older header-plus-JSON county file."""
    path = Path(path)
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as handle:
        handle.readline()
        handle.readline()
        buf = ""
        pos = 0
        eof = False

        def fill(min_have):
            nonlocal buf, pos, eof
            if pos > 1_000_000:
                buf = buf[pos:]
                pos = 0
            while not eof and len(buf) - pos < min_have:
                chunk = handle.read(1 << 20)
                if not chunk:
                    eof = True
                    break
                buf += chunk

        def skip_ws():
            nonlocal pos
            while True:
                fill(1)
                if pos >= len(buf):
                    return
                if buf[pos] in " \t\r\n":
                    pos += 1
                else:
                    return

        skip_ws()
        fill(1)
        if pos >= len(buf) or buf[pos] != "[":
            raise CountyDownloadError(f"{path.name} does not contain a feature list")
        pos += 1
        while True:
            skip_ws()
            fill(1)
            if pos >= len(buf):
                raise CountyDownloadError(f"{path.name} ended before the feature list closed")
            if buf[pos] == "]":
                break
            if buf[pos] == ",":
                pos += 1
                continue
            while True:
                try:
                    feature, end = decoder.raw_decode(buf, pos)
                    pos = end
                    break
                except json.JSONDecodeError:
                    if eof:
                        raise
                    fill((1 << 20))
            if not isinstance(feature, dict):
                raise CountyDownloadError(f"{path.name} contains a record that is not an object")
            yield feature


def convert_legacy_py_file(py_path, sqlite_path, cfg, updated_stamp):
    """Turn one finished JSON county file into a SQLite file with the same date."""
    counter = {"n": 0}

    def batches():
        group = []
        for feature in iter_legacy_features(py_path):
            counter["n"] += 1
            group.append(feature)
            if len(group) >= 2000:
                yield group
                group = []
        if group:
            yield group

    written = write_feature_batches(
        sqlite_path, batches(), cfg, finished_stamp=updated_stamp, synchronous="NORMAL",
    )
    if written != counter["n"]:
        raise CountyDownloadError(
            f"{Path(py_path).name} converted {written} rows from {counter['n']} records"
        )
    return written


def adopt_legacy_county_file(state, county):
    """Build the SQLite file from a finished .py file when the new file is missing."""
    dest = county_file_path(current_data_dir(), state, county)
    if current_file_is_ready(state, county):
        return False
    legacy = dest.with_suffix(".py")
    header = _peek_py_header(legacy) if legacy.is_file() else None
    if not header or not header[1]:
        return False
    convert_legacy_py_file(legacy, dest, CONFIG[state][county], header[0])
    return current_file_is_ready(state, county)


def delete_unfinished_files(directory):
    """Delete every file in the directory that did not finish downloading.

    finished is false, a missing or unreadable header, a partial file, and a
    replacing file are unfinished. A finished county file is kept.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for path in list(directory.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if (
            name.endswith(".replacing")
            or name.endswith("-journal")
            or name.endswith("-wal")
            or name.endswith("-shm")
            or path.suffix == ".partial"
        ):
            unfinished = True
        elif path.suffix in {".py", ".sqlite"}:
            header = peek_county_header(path)
            unfinished = header is None or not header[1]
        else:
            unfinished = False
        if not unfinished:
            continue
        try:
            path.unlink()
        except OSError:
            pass


def clear_directory(directory):
    directory = Path(directory)
    if directory.exists():
        for path in directory.iterdir():
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
    directory.mkdir(parents=True, exist_ok=True)


def current_file_is_ready(state, county):
    path = county_file_path(current_data_dir(), state, county)
    if not path.is_file():
        return False
    header = peek_county_header(path)
    return bool(header and header[1])


def _current_file_is_from_this_session(state, county):
    """True when the file's updated stamp was written during this launch."""
    path = county_file_path(current_data_dir(), state, county)
    header = peek_county_header(path) if path.is_file() else None
    if not header:
        return False
    try:
        stamp = datetime.strptime(header[0], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    return stamp >= _session_started.replace(microsecond=0)


def refresh_candidates():
    """Counties whose saved date is older than this launch, oldest first.

    The clock moving forward does not make a county due again. A file written
    during this launch is skipped, and so is a county this launch already
    queued. A county with no finished file is skipped. It does not block the
    refresh, and this pass does not create a current file for it. The oldest
    saved date is first so a later launch continues with whoever has waited longest.
    """
    ranked = []
    for state, county in iter_implemented_counties():
        if (state, county) in _session_refresh_claimed:
            continue
        if not current_file_is_ready(state, county):
            continue
        if _current_file_is_from_this_session(state, county):
            continue
        path = county_file_path(current_data_dir(), state, county)
        header = peek_county_header(path)
        stamp = header[0] if header else ""
        ranked.append((stamp, state, county))
    ranked.sort()
    return [(state, county) for _stamp, state, county in ranked]


class CountyDownloadError(Exception):
    pass


class CountyQueryError(CountyDownloadError):
    """The server answered, but the query itself was rejected."""


def _read_query(req, timeout):
    try:
        with _urlopen(req, timeout=timeout) as response:
            payload = _parse_arcgis_json(response.read())
    except Exception as exc:
        raise CountyDownloadError(str(exc)) from exc
    if isinstance(payload, dict) and payload.get("error"):
        err = payload["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise CountyQueryError(message or "The county database returned an error")
    return payload


def _query_json(base_url, params, timeout=60):
    """POST a layer query. Esri documents returnIdsOnly as the way to list every
    object id, then objectIds to fetch those records in maxRecordCount-sized groups.
    POST keeps a large id list from overflowing the URL. GET is the fallback for
    servers that reject POST, which is how the old search called these layers."""
    body = urlencode(params, quote_via=quote).encode("utf-8")
    post = urllib.request.Request(base_url, data=body, headers={"User-Agent": "Mozilla/5.0"})
    try:
        try:
            return _read_query(post, timeout)
        except CountyQueryError:
            raise
        except CountyDownloadError:
            query = urlencode(params, quote_via=quote)
            get = urllib.request.Request(f"{base_url}?{query}", headers={"User-Agent": "Mozilla/5.0"})
            return _read_query(get, timeout)
    finally:
        _polite_pause()


def _as_int_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


def _dedupe_ids(raw_ids):
    collected = []
    seen = set()
    for item in raw_ids:
        number = _as_int_id(item)
        if number is None:
            raise CountyDownloadError("The county database returned a non-numeric object id")
        if number in seen:
            continue
        seen.add(number)
        collected.append(number)
    return collected


def _feature_object_id(feature, field):
    attrs = feature.get("attributes") or {}
    if field in attrs:
        return _as_int_id(attrs.get(field))
    for key, value in attrs.items():
        if str(key).lower() == str(field).lower():
            return _as_int_id(value)
    return None


def _layer_count(base_url):
    """Esri's returnCountOnly count. None when this service does not provide one."""
    try:
        payload = _query_json(base_url, {
            "where": "1=1",
            "returnCountOnly": "true",
            "f": "pjson",
        })
    except CountyDownloadError:
        return None
    count = payload.get("count") if isinstance(payload, dict) else None
    return count if isinstance(count, int) and count >= 0 else None


def _transfer_limit(payload):
    """True when Esri says this page is incomplete, False when it says this page
    is the end, None when the server left the flag off."""
    if not isinstance(payload, dict) or "exceededTransferLimit" not in payload:
        return None
    flag = payload.get("exceededTransferLimit")
    if flag is True or flag == 1:
        return True
    if flag is False or flag == 0:
        return False
    if isinstance(flag, str):
        lowered = flag.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None


def _page_is_last(payload, received, page_size):
    """A short page ends the walk only when the server is not saying more remain.

    exceededTransferLimit true means keep going, even when this page is shorter
    than requested. An explicit false means this response is the end, even when
    the page is full. A missing flag ends the walk only on a short or empty page.
    """
    limit = _transfer_limit(payload)
    if limit is True:
        return False
    if limit is False:
        return True
    return received < page_size


def _require_forward_ids(numbers, last, what):
    """The next page must be strictly ascending and start after the previous max.

    Taking max() of an unsorted page would skip every id that was left out below
    that max. Fail instead of saving a file with those records missing.
    """
    for index in range(1, len(numbers)):
        if numbers[index] <= numbers[index - 1]:
            raise CountyDownloadError(f"{what} pagination was not in ascending order")
    if last is not None and numbers[0] <= last:
        raise CountyDownloadError(f"{what} pagination did not advance")


def _fields_including_oid(out_fields, object_id_field):
    text = out_fields or "*"
    if text.strip() == "*":
        return text
    names = [part.strip() for part in text.split(",") if part.strip()]
    if any(name.lower() == str(object_id_field).lower() for name in names):
        return text
    return text + "," + str(object_id_field)


def _page_object_ids(base_url, page_size, field, expected=None):
    """Read every object id in object-id order.

    The next page is `{field} > lastMax`, not resultOffset. A short page therefore
    cannot jump past the ids that follow the ones actually returned.
    """
    collected = []
    seen = set()
    last = None
    while True:
        where = "1=1" if last is None else f"{field} > {last}"
        payload = _query_json(base_url, {
            "where": where,
            "returnIdsOnly": "true",
            "returnGeometry": "false",
            "orderByFields": f"{field} ASC",
            "resultRecordCount": page_size,
            "f": "pjson",
        })
        raw = payload.get("objectIds") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            raise CountyDownloadError("The county database did not return an object id list")
        if not raw:
            if _transfer_limit(payload) is True:
                raise CountyDownloadError("The county database paged an empty object id list")
            break
        numbers = []
        for item in raw:
            number = _as_int_id(item)
            if number is None:
                raise CountyDownloadError("The county database returned a non-numeric object id")
            if number in seen:
                raise CountyDownloadError("Object id pagination repeated an id")
            seen.add(number)
            numbers.append(number)
        _require_forward_ids(numbers, last, "Object id")
        collected.extend(numbers)
        if expected is not None and len(collected) > expected:
            raise CountyDownloadError(
                f"Object id pagination returned more than the service count of {expected}"
            )
        last = numbers[-1]
        if _page_is_last(payload, len(numbers), page_size):
            break
    return collected


def _collect_object_ids(base_url, page_size, object_id_field):
    """Esri documents returnIdsOnly as the complete id list. A capped list is
    discarded and read again in order so the cap cannot drop ids in the middle."""
    expected = _layer_count(base_url)
    payload = _query_json(base_url, {
        "where": "1=1",
        "returnIdsOnly": "true",
        "returnGeometry": "false",
        "f": "pjson",
    })
    field = object_id_field
    if isinstance(payload, dict) and payload.get("objectIdFieldName"):
        field = payload["objectIdFieldName"]
    raw = payload.get("objectIds") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        raise CountyDownloadError("The county database did not return an object id list")
    if _transfer_limit(payload) is True:
        ids = _page_object_ids(base_url, page_size, field, expected)
    else:
        ids = _dedupe_ids(raw)
    if expected is not None and len(ids) != expected:
        raise CountyDownloadError(
            f"Object id list has {len(ids)} ids but the service count is {expected}"
        )
    return ids, field


def _features_for_ids(base_url, ids, out_fields, object_id_field):
    """Fetch one feature per object id. A short page is requested again in smaller
    pieces instead of being saved with the missing records left out."""
    if not ids:
        return []
    fields = _fields_including_oid(out_fields, object_id_field)
    try:
        payload = _query_json(base_url, {
            "objectIds": ",".join(str(item) for item in ids),
            "outFields": fields,
            "returnGeometry": "false",
            "f": "pjson",
        })
    except CountyQueryError:
        raise
    except CountyDownloadError:
        if len(ids) == 1:
            raise
        mid = len(ids) // 2
        return (
            _features_for_ids(base_url, ids[:mid], out_fields, object_id_field)
            + _features_for_ids(base_url, ids[mid:], out_fields, object_id_field)
        )
    batch = payload.get("features") or []
    found = {}
    unlabeled = []
    for feature in batch:
        number = _feature_object_id(feature, object_id_field)
        if number is None:
            unlabeled.append(feature)
        else:
            found[number] = feature
    # Some layers omit the object id from outFields. A full unlabeled page is
    # accepted only when every requested id has a record.
    if unlabeled and not found and len(unlabeled) == len(ids):
        return unlabeled
    missing = [item for item in ids if item not in found]
    if missing or len(batch) != len(ids):
        if len(ids) == 1:
            raise CountyDownloadError("The county database omitted a requested record")
        mid = len(ids) // 2
        return (
            _features_for_ids(base_url, ids[:mid], out_fields, object_id_field)
            + _features_for_ids(base_url, ids[mid:], out_fields, object_id_field)
        )
    return [found[item] for item in ids]


def _iter_feature_keyset(base_url, page_size, object_id_field, out_fields):
    """Page features with `objectId > last` when the id list itself is unavailable.

    resultOffset is not used. A short page is continued when exceededTransferLimit
    is true, so the records after the ones actually returned are not skipped.
    """
    fields = _fields_including_oid(out_fields, object_id_field)
    seen = set()
    last = None
    total = 0
    expected = _layer_count(base_url)
    while True:
        where = "1=1" if last is None else f"{object_id_field} > {last}"
        payload = _query_json(base_url, {
            "where": where,
            "outFields": fields,
            "returnGeometry": "false",
            "orderByFields": f"{object_id_field} ASC",
            "resultRecordCount": page_size,
            "f": "pjson",
        })
        batch = payload.get("features") or []
        if not batch:
            if _transfer_limit(payload) is True:
                raise CountyDownloadError("The county database paged an empty feature list")
            break
        numbers = []
        fresh = []
        for feature in batch:
            number = _feature_object_id(feature, object_id_field)
            if number is None:
                raise CountyDownloadError("A downloaded record is missing its object id")
            if number in seen:
                raise CountyDownloadError("Feature pagination repeated an id")
            seen.add(number)
            numbers.append(number)
            fresh.append(feature)
        _require_forward_ids(numbers, last, "Feature")
        yield fresh
        total += len(fresh)
        if expected is not None and total > expected:
            raise CountyDownloadError(
                f"Downloaded more than the service count of {expected}"
            )
        last = numbers[-1]
        if _page_is_last(payload, len(batch), page_size):
            break
    if expected is not None and total != expected:
        raise CountyDownloadError(
            f"Downloaded {total} records but the service count is {expected}"
        )


def iter_layer_batches(base_url, page_size, object_id_field, out_fields="*"):
    """Yield every feature for where=1=1. Every object id is fetched. A download
    that cannot account for each id raises instead of saving a short file."""
    page_size = max(1, int(page_size or 2000))
    try:
        ids, field = _collect_object_ids(base_url, page_size, object_id_field)
    except CountyQueryError:
        raise
    except CountyDownloadError:
        yield from _iter_feature_keyset(base_url, page_size, object_id_field, out_fields)
        return

    if not ids:
        raise CountyDownloadError("The county database returned no records")
    fetched = 0
    for start in range(0, len(ids), page_size):
        chunk = ids[start:start + page_size]
        features = _features_for_ids(base_url, chunk, out_fields, field)
        if len(features) != len(chunk):
            raise CountyDownloadError(
                f"Expected {len(chunk)} records for this page and received {len(features)}"
            )
        fetched += len(features)
        yield features
    if fetched != len(ids):
        raise CountyDownloadError(
            f"Fetched {fetched} records from an id list of {len(ids)}"
        )


def _attach_deschutes_owners(owners, details):
    """Same owner/taxlot join the live Deschutes search uses."""
    taxlot_to_owner = {}
    for owner_feature in owners:
        attrs = owner_feature.get("attributes") or {}
        taxlot = attrs.get("map_taxlot")
        name = attrs.get("name")
        if taxlot is None or not name:
            continue
        if "," in name and ", " not in name:
            name = name.replace(",", ", ")
        taxlot_to_owner[str(taxlot).strip()] = name
    joined = []
    for detail in details:
        attrs = detail.setdefault("attributes", {})
        taxlot = attrs.get("taxlot")
        owner = taxlot_to_owner.get(str(taxlot).strip()) if taxlot is not None else None
        if owner:
            attrs["name"] = owner
        attrs["state"] = "OR"
        joined.append(detail)
    return joined


def iter_county_batches(cfg):
    page = cfg.get("max_record_count", 2000)
    if cfg.get("is_two_step"):
        owners = []
        for batch in iter_layer_batches(cfg["base_url_owners"], page, "objectid", "map_taxlot,name"):
            owners.extend(batch)
        details = []
        for batch in iter_layer_batches(cfg["base_url_details"], page, "objectid", "*"):
            details.extend(batch)
        yield _attach_deschutes_owners(owners, details)
        return
    oid = cfg.get("object_id_field", "OBJECTID")
    if cfg.get("supports_expanded"):
        features = []
        for batch in iter_layer_batches(cfg["base_url"], page, oid, "*"):
            features.extend(batch)
        yield enrich_expanded_addresses(features, cfg)
        return
    yield from iter_layer_batches(cfg["base_url"], page, oid, "*")


def download_county_file(cfg, path, polite=False):
    """Download one county. On failure the file is left with finished = False
    so the next launch can see it, delete it, and try again.

    polite=True is the background refresh: it yields so clicks and searches
    stay ahead of it. A first download of a missing county stays at full speed.
    """
    global _polite_download
    path = Path(path)
    previous = _polite_download
    _polite_download = bool(polite)
    try:
        write_feature_batches(path, iter_county_batches(cfg), cfg)
    finally:
        _polite_download = previous


def leave_download_unfinished(path):
    """A county that could not be downloaded stays finished = 0.

    A file that already has a readable header is left alone, including one
    that already finished. Only a missing or unreadable file gets a stub.
    """
    path = Path(path)
    if path.is_file() and peek_county_header(path) is not None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    con = _connect_county_db(path)
    try:
        con.executescript(_COUNTY_SCHEMA)
        con.execute(
            "INSERT INTO meta(key, value) VALUES ('updated', ?), ('finished', '0')",
            (_now_stamp(),),
        )
        con.commit()
    finally:
        con.close()
    _fsync_dir(path.parent)


def _field_text(attrs, field):
    if not field:
        return ""
    value = attrs.get(field)
    if value is None:
        return ""
    return str(value)


def _deschutes_address_match(attrs, address):
    """Same rules as build_deschutes_address_where. The SQL does not wrap fields in UPPER()."""
    parts = address.split()
    house_num = None
    remaining = parts[:]
    if parts and parts[0].isdigit():
        house_num = parts[0]
        remaining = parts[1:]
    common_types = {"ST", "AVE", "RD", "DR", "LN", "CT", "WAY", "BLVD", "PL", "TER", "CIR", "LOOP", "PKWY", "HWY"}
    street_type = remaining[-1] if remaining and remaining[-1] in common_types else None
    street_name_parts = remaining[:-1] if street_type else remaining
    street_name = " ".join(street_name_parts) if street_name_parts else None
    if house_num is None and street_name is None and street_type is None:
        return address in _field_text(attrs, "address")
    if house_num is not None and str(attrs.get("house_number")).strip() != house_num:
        return False
    if street_name is not None and street_name not in _field_text(attrs, "street_name"):
        return False
    if street_type is not None and _field_text(attrs, "street_type") != street_type:
        return False
    return True


def record_matches(attrs, name_input, address_input, cfg):
    """Local equivalent of the ArcGIS WHERE the live search used to send."""
    name = " ".join(name_input.strip().upper().split()) if name_input else ""
    address = " ".join(address_input.strip().upper().split()) if address_input else ""
    if name:
        owner_fields = cfg.get("owner_field", "NAME")
        if not isinstance(owner_fields, list):
            owner_fields = [owner_fields]
        seen = set()
        for word in re.split(r"\s+", name):
            word = word.strip(",.")
            if not word or len(word) == 1 or word in seen:
                continue
            seen.add(word)
            if not any(word in _field_text(attrs, field).upper() for field in owner_fields):
                return False
    if address and cfg.get("is_two_step"):
        return _deschutes_address_match(attrs, address)
    if address:
        haystack = _field_text(attrs, cfg.get("address_field")).upper()
        parts = address.split()
        if parts and parts[0].isdigit():
            if not haystack.startswith(parts[0] + " "):
                return False
            rest = parts[1:]
        else:
            rest = parts
        for part in rest:
            if part and part not in haystack:
                return False
    return True


def filter_county_features(features, name_input, address_input, cfg):
    matched = []
    for feature in features:
        attrs = feature.get("attributes") or {}
        if record_matches(attrs, name_input, address_input, cfg):
            matched.append(feature)
    return matched


_STREET_TYPES = {
    "ST", "AVE", "RD", "DR", "LN", "CT", "WAY", "BLVD", "PL", "TER", "CIR", "LOOP", "PKWY", "HWY",
}


def _name_words(name_input):
    name = " ".join(name_input.strip().upper().split()) if name_input else ""
    words = []
    seen = set()
    for word in re.split(r"\s+", name):
        word = word.strip(",.")
        if not word or len(word) == 1 or word in seen:
            continue
        seen.add(word)
        words.append(word)
    return words


def _prefix_upper_bound(prefix):
    """First string that does not start with prefix."""
    return prefix[:-1] + chr(ord(prefix[-1]) + 1)


def _match_query(name_input, address_input, cfg):
    """SQL for the same match record_matches uses. Parameters stay literal."""
    address = " ".join(address_input.strip().upper().split()) if address_input else ""
    clauses = []
    params = []
    for word in _name_words(name_input):
        clauses.append("instr(owner, ?) > 0")
        params.append(word)
    if address and cfg.get("is_two_step"):
        parts = address.split()
        house_num = None
        remaining = parts[:]
        if parts and parts[0].isdigit():
            house_num = parts[0]
            remaining = parts[1:]
        street_type = remaining[-1] if remaining and remaining[-1] in _STREET_TYPES else None
        street_name_parts = remaining[:-1] if street_type else remaining
        street_name = " ".join(street_name_parts) if street_name_parts else None
        if house_num is None and street_name is None and street_type is None:
            clauses.append("instr(address, ?) > 0")
            params.append(address)
        else:
            if house_num is not None:
                clauses.append("house_number = ?")
                params.append(house_num)
            if street_name is not None:
                clauses.append("instr(street_name, ?) > 0")
                params.append(street_name)
            if street_type is not None:
                clauses.append("street_type = ?")
                params.append(street_type)
    elif address:
        parts = address.split()
        if parts and parts[0].isdigit():
            prefix = parts[0] + " "
            clauses.append("address >= ? AND address < ?")
            params.append(prefix)
            params.append(_prefix_upper_bound(prefix))
            parts = parts[1:]
        for part in parts:
            if part:
                clauses.append("instr(address, ?) > 0")
                params.append(part)
    where = " AND ".join(clauses) if clauses else "1"
    return where, params


def _open_county_query(path, name_input, address_input, cfg):
    header = peek_county_header(path)
    if header is None or not header[1]:
        raise CountyDownloadError(f"{Path(path).name} is not a finished county file")
    where, params = _match_query(name_input, address_input, cfg)
    con = _connect_county_db(path, readonly=True)

    def yield_to_ui():
        time.sleep(0)
        return 0

    con.set_progress_handler(yield_to_ui, 200_000)
    return con, where, params


def query_county_match(path, name_input, address_input, cfg, limit=None):
    """True match count, plus at most *limit* attribute rows for display."""
    con, where, params = _open_county_query(path, name_input, address_input, cfg)
    try:
        row = con.execute(f"SELECT COUNT(*) FROM parcels WHERE {where}", params).fetchone()
        nmatch = int(row[0] if row else 0)
        if not nmatch or limit == 0:
            return nmatch, []
        max_rows = nmatch if limit is None else max(int(limit), 0)
        features = []
        cursor = con.execute(
            f"SELECT attributes FROM parcels WHERE {where} ORDER BY id", params,
        )
        while True:
            chunk = cursor.fetchmany(50)
            if not chunk:
                break
            for (raw,) in chunk:
                try:
                    attrs = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise CountyDownloadError(
                        f"{Path(path).name} has a damaged record"
                    ) from exc
                features.append({"attributes": attrs})
                if len(features) >= max_rows:
                    return nmatch, features
            time.sleep(0)
        return nmatch, features
    finally:
        con.close()


def query_county_count(path, name_input, address_input, cfg):
    """How many parcels match. Does not load attribute blobs."""
    nmatch, _features = query_county_match(
        path, name_input, address_input, cfg, limit=0,
    )
    return nmatch


def query_county_features(path, name_input, address_input, cfg, limit=None):
    """Matching parcels only. The whole county file is not loaded."""
    _nmatch, features = query_county_match(
        path, name_input, address_input, cfg, limit=limit,
    )
    return features


def _county_in_state(state, county):
    return f"{_county_sentence_name(county)} county in {state}"


def offline_county_message(county, state=None):
    place = _county_in_state(state, county) if state else county
    return (
        f"{place} cannot be displayed because it could not be downloaded because its database is offline, "
        "so it cannot be searched.\n"
    )


def statewide_preface_lines(stale_lines, missing_counties):
    """Offline-file notices sit at the top, then last-updated lines.

    One blank line separates the two groups.
    """
    offline = [offline_county_message(cname).rstrip("\n") for cname in missing_counties]
    lines = list(offline)
    if offline and stale_lines:
        lines.append("")
    lines.extend(stale_lines)
    return lines


def _cap_display_list(items):
    """Keep the first 10000 display rows. True when more existed."""
    overflow = len(items) > RESULT_DISPLAY_CAP
    if overflow:
        items = items[:RESULT_DISPLAY_CAP]
    return items, overflow


def _overflow_header_lines(overflow, total=None):
    if overflow:
        return [overflow_warning_line(total), ""]
    return []


def _pin_overflow_notes(overflow, extra_lines=None, total=None):
    notes = []
    if overflow:
        notes.append(overflow_warning_line(total))
    extra = []
    for line in extra_lines or []:
        text = str(line).rstrip("\n")
        if text.strip():
            extra.append(text)
    if notes and extra:
        notes.append("")
    notes.extend(extra)
    return notes


def statewide_result_header_lines(
    preface_lines, unavail, zero_counties, county_counts, all_county_names, state,
    overflow=False, total=None,
):
    """Statewide results header.

    Offline-file notices sit at the top, then last-updated, then not-searchable.
    One blank line separates those groups. An overflow warning, when present,
    is the first line.
    """
    if total is None:
        total = sum(county_counts.values())
    lines = []
    lines.extend(_overflow_header_lines(overflow, total))
    if preface_lines:
        lines.extend(preface_lines)
        lines.append("")
    n_unavail = len(unavail)
    if n_unavail:
        lines.append(
            f"The following {n_unavail} {_count_noun(n_unavail, 'county', 'counties')} "
            f"in {state} {_count_noun(n_unavail, 'is', 'are')} not searchable:"
        )
        lines.extend(unavail)
        lines.append("")
    n_zero = len(zero_counties)
    if n_zero:
        lines.append(
            f"The following {n_zero} {_count_noun(n_zero, 'county', 'counties')} "
            f"did not return any results:"
        )
        lines.extend(zero_counties)
        lines.append("")
    hit_counties = len(county_counts)
    lines.append(
        f"{total} {_count_noun(total, 'result')} found in {hit_counties} "
        f"{_count_noun(hit_counties, 'county', 'counties')}:"
    )
    for cname in all_county_names:
        if cname in county_counts:
            cnt = county_counts[cname]
            lines.append(f"{cnt} {_count_noun(cnt, 'result')} found in {cname}")
    return lines


def selection_needs_offline_notice(state, county):
    """The selected county has no finished file the search can use."""
    if not county or county == "Statewide":
        return False
    if county not in CONFIG.get(state, {}):
        return False
    if current_file_is_ready(state, county):
        return False
    if not searches_allowed and (state, county) not in _counties_download_failed:
        return False
    return True


def note_offline_selection():
    """Show the offline sentence when the dropdown lands on that county."""
    try:
        widget = output
        state = state_var.get()
        county = county_var.get()
    except (NameError, tk.TclError):
        return
    if not selection_needs_offline_notice(state, county):
        return
    message = offline_county_message(county)
    try:
        current = widget.get("1.0", "end-1c")
    except tk.TclError:
        return
    if message.strip() in current:
        return
    append_output(message)


def _county_sentence_name(county):
    if county.endswith(" County"):
        return county[: -len(" County")]
    return county


def stale_notice_line(state, county):
    """Top-of-results line when this launch has not replaced the county file."""
    if not county or county == "Statewide":
        return None
    if county not in CONFIG.get(state, {}):
        return None
    if not current_file_is_ready(state, county):
        return None
    if _current_file_is_from_this_session(state, county):
        return None
    path = county_file_path(current_data_dir(), state, county)
    header = peek_county_header(path) if path.is_file() else None
    if not header:
        return None
    name = _county_sentence_name(county)
    return f"Data for {name} county was last updated {format_updated_for_user(header[0])}."


def countrywide_summary_lines(n_missing, n_unavail, n_zero, total, hit_states, county_hits):
    """Compact countrywide notices, then the results-by-state list.

    county_hits is [(state, county, count), ...] in CONFIG order.
    """
    lines = [
        (
            f"{n_missing} implemented {_count_noun(n_missing, 'county', 'counties')} "
            f"{_count_noun(n_missing, 'is', 'are')} not downloaded to file."
        ),
        (
            f"{n_unavail} {_count_noun(n_unavail, 'county', 'counties')} "
            f"{_count_noun(n_unavail, 'is', 'are')} not searchable."
        ),
        (
            f"{n_zero} implemented {_count_noun(n_zero, 'county', 'counties')} "
            f"did not return any results."
        ),
        "",
        (
            f"{total} {_count_noun(total, 'result')} found in "
            f"{hit_states} {_count_noun(hit_states, 'state')} and "
            f"{len(county_hits)} {_count_noun(len(county_hits), 'county', 'counties')}:"
        ),
    ]
    for state, county, count in county_hits:
        name = _county_sentence_name(county)
        lines.append(
            f"{state}: {count} {_count_noun(count, 'result')} found in {name} county."
        )
    return lines


def fetch_all_results(name_input, address_input, cfg=None):
    if cfg is None:
        cfg = get_cfg()
    fetch_paginated.last_error = None
    if cfg.get("is_two_step"):
        return _fetch_deschutes(name_input, address_input, cfg)
    where = build_where_clause(name_input, address_input, cfg)
    oid = cfg.get("object_id_field", "OBJECTID")
    features = fetch_paginated(cfg["base_url"], where, cfg.get("max_record_count", 2000), oid)
    return features, {}


def _empty_search_message():
    """Prefer a network/SSL error message over a misleading 'no results'."""
    err = getattr(fetch_paginated, "last_error", None)
    if err:
        return (
            f"Network error (no data received):\n{err}\n\n"
            "If this is a freshly copied .app, rebuild with certifi included:\n"
            "  pip install certifi\n"
            "  pyinstaller --noconfirm --windowed --name DataGrabber "
            "--clean --collect-data certifi DataGrabber.py\n"
        )
    return "No matching properties found.\n"


def resolve_deschutes_full(name_input, address_input, cfg):
    """Fully resolve all Deschutes taxlots into feature dicts (no batching)."""
    taxlots, taxlot_map = _fetch_deschutes(name_input, address_input, cfg)
    features = []
    for taxlot in taxlots:
        safe = str(taxlot).replace("'", "''")
        where = f"taxlot = '{safe}'"
        params = {
            "where": where,
            "outFields": "*",
            "returnGeometry": "false",
            "f": "pjson",
        }
        url = f"{cfg['base_url_details']}?{urlencode(params, quote_via=quote)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urlopen(req, timeout=12) as response:
                data = _parse_arcgis_json(response.read())
                for d in data.get("features", []):
                    owner = taxlot_map.get(taxlot, "Unknown Owner")
                    if "," in owner and ", " not in owner:
                        owner = owner.replace(",", ", ")
                    d["attributes"]["name"] = owner
                    d["attributes"]["state"] = "OR"
                    features.append(d)
        except Exception:
            continue
        time.sleep(0.05)
    return features


# ───────────────────────────── UI helpers ─────────────────────────────
# tag name → url for clickable links in the results window
_link_urls = {}


def maps_url_for_query(loc_query, kind=None):
    """Browser URL for the current Map Link Type, using the result address."""
    if kind is None:
        try:
            kind = map_link_type_var.get()
        except (NameError, tk.TclError):
            kind = MAP_LINK_GOOGLE_MAPS
    query = loc_query if loc_query is not None else ""
    if kind == MAP_LINK_GOOGLE_EARTH:
        return f"https://earth.google.com/web/search/{quote(query, safe=',')}/"
    if kind == MAP_LINK_APPLE_MAPS:
        return f"https://maps.apple.com/?q={quote_plus(query)}"
    return f"https://www.google.com/maps?q={quote_plus(query)}"


def _clean_view_on():
    try:
        return bool(clean_view_var.get())
    except (NameError, tk.TclError):
        return False


def _output_has_search_results():
    """True when the results window is showing (or still loading) a search."""
    if displayed_results or total_results:
        return True
    try:
        label = loading_label.cget("text")
    except (NameError, tk.TclError):
        label = ""
    if str(label).startswith("Loading results") or str(label).startswith("Displaying results"):
        return True
    try:
        snippet = output.get("1.0", "120.0")
    except (tk.TclError, NameError):
        return False
    return "─── Result" in snippet or "matching properties" in snippet


def on_clean_view_toggle():
    """Re-run the current search so cards redraw in the other format."""
    if not _output_has_search_results():
        return
    try:
        has_name = bool(name_entry.get().strip())
        has_addr = bool(address_entry.get().strip())
    except (NameError, tk.TclError):
        return
    if not has_name and not has_addr:
        return
    search_name()


def _on_check_label(variable, command, event=None):
    try:
        variable.set(not bool(variable.get()))
    except (tk.TclError, NameError):
        return
    if command is not None:
        command()
    return "break"


def _pack_labeled_checkbutton(parent, text, variable, command, **pack_kwargs):
    """Native box + separate label. The box sits 2px lower so it meets the text midline."""
    row = tk.Frame(parent)
    box = tk.Checkbutton(
        row,
        variable=variable,
        command=command,
        highlightthickness=0,
        bd=0,
        padx=0,
        pady=0,
        takefocus=0,
    )
    box.pack(side="left", pady=(_CHECKBOX_Y_NUDGE, 0), anchor="n")
    label = tk.Label(
        row,
        text=text,
        font=("Arial", 12),
        fg="#ffffff",
        anchor="w",
        justify="left",
        padx=0,
        pady=0,
        bd=0,
        highlightthickness=0,
    )
    label.pack(side="left", anchor="n")
    label.bind("<Button-1>", lambda e: _on_check_label(variable, command, e))
    row.pack(**pack_kwargs)
    return row, box, label


def open_link(url, event=None):
    """Open url in the user's default browser (Safari, Chrome, Firefox, etc.)."""
    # new=2 → open in a new tab if the browser supports it
    webbrowser.open(url, new=2)


def set_hand_cursor(w):
    w.config(cursor="pointinghand")


def set_arrow_cursor(w):
    w.config(cursor="arrow")


def _insert_clickable_text(text, url, tag):
    """Insert *text* as a clickable link. Only the text characters are tagged
    (linestart → lineend), not empty space after the line.
    Caller is responsible for output state (NORMAL while inserting)."""
    output.insert(tk.END, text + "\n")
    output.tag_add(tag, "end-2l linestart", "end-2l lineend")
    output.tag_config(tag, foreground="#7EC8E3", underline=1)
    _link_urls[tag] = url
    output.tag_bind(tag, "<Enter>", lambda e: set_hand_cursor(output))
    output.tag_bind(tag, "<Leave>", lambda e: set_arrow_cursor(output))


def _on_output_click(event):
    """Central click handler for links — works even when the Text widget is
    DISABLED (tag_bind alone is unreliable on some macOS Tk builds)."""
    if _open_choice is not None:
        _open_choice.close()
    try:
        idx = output.index(f"@{event.x},{event.y}")
    except tk.TclError:
        return
    for tag in output.tag_names(idx):
        target = _link_urls.get(tag)
        if not target:
            continue
        if isinstance(target, tuple) and len(target) == 2 and target[0] == "maps":
            open_link(maps_url_for_query(target[1]))
        else:
            open_link(target)
        return "break"


def reset_to_search_mode():
    global current_search_name, current_search_county, all_matching_taxlots
    global taxlot_to_owner_map, displayed_results, total_results
    current_search_name = ""
    current_search_county = ""
    all_matching_taxlots = []
    taxlot_to_owner_map = {}
    displayed_results = 0
    total_results = 0
    try:
        search_button.config(text="Search", command=search_name)
        root.after(10, position_loading)
    except NameError:
        # Called during early GUI construction before widgets exist
        pass


def _restore_pinned_notes():
    """Put the last-updated lines back at the top if a redraw removed them."""
    notes = [str(line).rstrip("\n") for line in _pinned_result_notes]
    while notes and not notes[0].strip():
        notes.pop(0)
    while notes and not notes[-1].strip():
        notes.pop()
    if not notes:
        return
    leading = []
    for index in range(1, len(notes) + 1):
        start = f"{index}.0"
        if output.index(start) == output.index(tk.END):
            break
        leading.append(output.get(start, f"{index}.0 lineend").rstrip("\n"))
    if leading == notes:
        return
    found_at = []
    nonempty = {line for line in notes if line.strip()}
    line_no = 1
    while line_no <= 80:
        start = f"{line_no}.0"
        if output.index(start) == output.index(tk.END):
            break
        text = output.get(start, f"{line_no}.0 lineend").rstrip("\n")
        if text in nonempty:
            found_at.append(line_no)
        line_no += 1
    for line_no in reversed(found_at):
        output.delete(f"{line_no}.0", f"{line_no + 1}.0")
    output.insert("1.0", "\n".join(notes) + "\n\n")


def _matching_properties_line():
    if current_search_county in ("Statewide", COUNTRYWIDE):
        return f"Displaying {displayed_results}/{total_results} matching properties:"
    return (
        f"Displaying {displayed_results}/{total_results} matching properties "
        f"in {current_search_county}:"
    )


def _snapshot_yview():
    """First visible character. Stays on the same text after later inserts."""
    try:
        return output.index("@0,0")
    except (tk.TclError, NameError):
        return None


def _restore_yview(mark):
    if mark is None:
        return
    try:
        output.yview(mark)
    except (tk.TclError, NameError):
        pass


def _block_output_edit(event=None):
    return "break"


def _note_user_scrolled(event=None):
    global _user_scrolled
    _user_scrolled = True


def _bind_results_scroll():
    """Let Tk/Cocoa scroll the results. Python only records that the user moved.

    DISABLED text drops wheel events on macOS, and intercepting the wheel in
    Python (or via objc) both felt wrong and crashed on a fast spin.
    """
    output.config(state=tk.NORMAL, insertwidth=0, takefocus=0)
    for sequence in ("<Key>", "<<Paste>>", "<<Cut>>", "<<Clear>>", "<Button-2>"):
        output.bind(sequence, _block_output_edit)
    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        output.bind(sequence, _note_user_scrolled, add="+")
    vbar = getattr(output, "vbar", None)
    if vbar is not None:
        def _on_vbar_wheel(event):
            _note_user_scrolled()
            delta = int(getattr(event, "delta", 0) or 0)
            if abs(delta) >= 120:
                units = -(delta // 120)
            elif delta:
                units = -delta
            elif getattr(event, "num", None) == 4:
                units = -1
            elif getattr(event, "num", None) == 5:
                units = 1
            else:
                return "break"
            try:
                output.yview_scroll(int(units), "units")
            except tk.TclError:
                pass
            return "break"
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            vbar.bind(sequence, _on_vbar_wheel)
    try:
        output.bind("<TouchpadScroll>", _note_user_scrolled, add="+")
    except tk.TclError:
        pass


def update_header():
    header = _matching_properties_line()
    pos = None if _user_scrolled else _snapshot_yview()
    output.config(state=tk.NORMAL)
    try:
        if not _user_scrolled:
            _restore_pinned_notes()
        start = None
        try:
            if "hdr_displaying" in output.mark_names():
                start = output.index("hdr_displaying")
                line = output.get(start, f"{start} lineend")
                if line.startswith("Displaying"):
                    output.delete(start, f"{start} lineend")
                    output.insert(start, header)
                    return
        except tk.TclError:
            start = None
        line_no = 1
        found = False
        while True:
            start = f"{line_no}.0"
            end = f"{line_no + 1}.0"
            if output.index(start) == output.index(tk.END):
                break
            text = output.get(start, end).strip()
            if text.startswith("Displaying"):
                output.delete(start, end)
                output.insert(start, header + "\n")
                found = True
                break
            if text.startswith("─── Result"):
                output.insert(start, header + "\n\n")
                found = True
                break
            line_no += 1
            if line_no > 400:
                break
        if not found:
            output.insert(tk.END, header + "\n\n")
            start = output.index("end-2l linestart")
        output.mark_set("hdr_displaying", start)
        output.mark_gravity("hdr_displaying", tk.LEFT)
    finally:
        output.config(state=tk.NORMAL)
        if not _user_scrolled:
            _restore_yview(pos)


def clear_output():
    global _pinned_result_notes, _user_scrolled
    _pinned_result_notes = []
    _user_scrolled = False
    output.config(state=tk.NORMAL)
    output.delete(1.0, tk.END)
    output.config(state=tk.NORMAL)
    _link_urls.clear()


def append_output(text):
    output.config(state=tk.NORMAL)
    output.insert(tk.END, text)
    output.config(state=tk.NORMAL)
    output.see(tk.END)


def _show_pinned_message(text, notes, search_id):
    """Show a short result message and keep its last-updated lines."""
    global _pinned_result_notes
    if search_id != _search_id:
        return
    _pinned_result_notes = [line.strip() for line in notes if line and str(line).strip()]
    append_output(text)
    stop_loading(search_id)


def _build_name_search_url(base_url, owner_field):
    """ArcGIS query URL for a name-only search with INSERTNAME placeholder.

    Encoding matches the form browsers/ArcGIS expect: spaces as %20 (not +),
    and outFields=* left unencoded.  Some county servers (e.g. San Diego)
    reject the default urllib quote_plus style.
    """
    fields = owner_field if isinstance(owner_field, list) else [owner_field]
    # Match the manual form: FIELD LIKE '%INSERTNAME%'  (optionally OR'd)
    conditions = " OR ".join(f"{f} LIKE '%INSERTNAME%'" for f in fields)
    where_enc = quote(conditions, safe="")
    return (
        f"{base_url}?where={where_enc}"
        f"&outFields=*&returnGeometry=false&f=pjson"
    )


def _build_details_browse_url(base_url):
    """Generic details-layer query URL (no owner field on two-step details)."""
    return f"{base_url}?where=1%3D1&outFields=*&returnGeometry=false&f=pjson"


def _insert_clickable_url(url, tag_prefix, index):
    """Insert a clickable URL line into the output widget."""
    output.config(state=tk.NORMAL)
    _insert_clickable_text(url, url, f"{tag_prefix}_{index}")
    output.config(state=tk.NORMAL)


def _state_implementation_lines(state, include_state_name=True):
    """Implemented-count and unavailable-county lines for a state's link list."""
    searchable = list(CONFIG[state].keys())
    unavail = [c for c in unavailableCounties.get(state, []) if c]
    n_implemented = len(searchable) + len(unavail)
    n_max = maxCountyCount.get(state)
    where = f" in {state}" if include_state_name else ""
    if n_max is not None and n_implemented == n_max:
        total_line = (
            f"All {n_implemented} {_count_noun(n_implemented, 'county', 'counties')}"
            f"{where} have been implemented."
        )
    elif n_max is not None:
        total_line = (
            f"A total of {n_implemented}/{n_max} "
            f"{_count_noun(n_max, 'county', 'counties')}{where} "
            f"have been implemented so far."
        )
    else:
        total_line = (
            f"A total of {n_implemented} "
            f"{_count_noun(n_implemented, 'county', 'counties')}{where} "
            f"have been implemented so far."
        )
    lines = [total_line, ""]
    n_unavail = len(unavail)
    if n_unavail:
        lines.append(
            f"The following {n_unavail} {_count_noun(n_unavail, 'county', 'counties')}"
            f"{where} {_count_noun(n_unavail, 'is', 'are')} not searchable because "
            f"they do not display the owner name field in the taxlot database:"
        )
        lines.extend(unavail)
        lines.append("")
    return lines, searchable


def _append_county_search_links(state, counties, link_index):
    for cname in counties:
        cfg = CONFIG[state][cname]
        if cfg.get("is_two_step"):
            append_output(f"{cname} — Owners layer:\n")
            url = _build_name_search_url(cfg["base_url_owners"], cfg["owner_field"])
            _insert_clickable_url(url, "link_owners", link_index)
            link_index += 1
            append_output(f"\n{cname} — Details layer:\n")
            url = _build_details_browse_url(cfg["base_url_details"])
            _insert_clickable_url(url, "link_details", link_index)
            link_index += 1
            append_output("\n")
        else:
            append_output(f"{cname}:\n")
            url = _build_name_search_url(cfg["base_url"], cfg["owner_field"])
            _insert_clickable_url(url, "link", link_index)
            link_index += 1
            append_output("\n")
    return link_index


def show_county_search_links():
    """Clear results and show manual ArcGIS name-search links for the
    selected county (or every county when Statewide is selected)."""
    global _search_id
    # Cancel any live search so its results cannot overwrite these links
    _search_id += 1
    stop_loading()
    reset_to_search_mode()
    clear_output()

    state = state_var.get()
    county = county_var.get()

    if state == COUNTRYWIDE:
        link_index = 0
        states = list(CONFIG.keys())
        for i, st in enumerate(states):
            append_output(f"{st.upper()}:\n")
            intro, searchable = _state_implementation_lines(st, include_state_name=False)
            append_output("\n".join(intro) + "\n")
            link_index = _append_county_search_links(st, searchable, link_index)
            if i < len(states) - 1:
                # Last county already ends with one blank line; two more make three.
                append_output("\n\n")
        output.see("1.0")
        return

    if county == "Statewide":
        intro, searchable = _state_implementation_lines(state, include_state_name=True)
        append_output("\n".join(intro) + "\n")
        counties_to_show = searchable
    else:
        counties_to_show = [county]

    _append_county_search_links(state, counties_to_show, 0)
    output.see("1.0")


def _widget_is_under(widget, ancestor):
    while widget is not None:
        if widget == ancestor:
            return True
        widget = getattr(widget, "master", None)
    return False


def _click_is_in_open_choice(widget):
    choice = _open_choice
    if choice is None or widget is None:
        return False
    if _widget_is_under(widget, choice.button):
        return True
    panel = choice.panel
    return panel is not None and _widget_is_under(widget, panel)


def _keep_focus_off_root():
    """A field cannot take a click while the window itself holds focus."""
    try:
        current = root.focus_get()
    except (NameError, tk.TclError):
        return
    if current is not None and current is not root:
        try:
            if current.winfo_exists():
                return
        except tk.TclError:
            pass
    try:
        name_entry.focus_force()
    except (NameError, tk.TclError, AttributeError):
        pass


def _focus_entry(event):
    """Put the caret in the field on the click, including after a county change."""
    widget = event.widget
    try:
        widget.focus_force()
    except tk.TclError:
        try:
            widget.focus_set()
        except tk.TclError:
            pass


def clear_entry_selections(event=None):
    if event is None:
        if _open_choice is not None:
            _open_choice.close()
        _keep_focus_off_root()
        return
    # A click on the open list belongs to that list. Any other click closes
    # it and leaves it closed.
    if _click_is_in_open_choice(event.widget):
        return
    if _open_choice is not None:
        _open_choice.close()
    w = event.widget
    if w == output or w in (name_entry, address_entry):
        return
    for entry in (name_entry, address_entry):
        try:
            entry.selection_clear()
        except tk.TclError:
            pass
    try:
        output.tag_remove("sel", "1.0", tk.END)
    except tk.TclError:
        pass


def start_loading_message(text):
    global is_loading, blink_job
    loading_label.config(text=text)
    if is_loading:
        # Already blinking — only the text changes (Statewide county switches).
        # Do NOT cancel/restart the timer or the blink becomes erratic.
        return
    is_loading = True
    loading_dot.config(fg="#cc0000")
    # Ensure no leftover timer from a previous cycle
    if blink_job:
        try:
            root.after_cancel(blink_job)
        except Exception:
            pass
        blink_job = None
    blink_loading_dot()


def start_loading(county_name):
    start_loading_message(f"Loading results for {county_name}")


def stop_loading(expected_id=None):
    """Stop the loading indicator. If expected_id is given, only stop when it
    still matches the current _search_id (so a superseded search cannot kill
    the indicator of a newer search)."""
    global is_loading, blink_job
    if _holding_download_status:
        return
    if expected_id is not None and expected_id != _search_id:
        return
    is_loading = False
    if blink_job:
        try:
            root.after_cancel(blink_job)
        except Exception:
            pass
        blink_job = None
    loading_dot.config(fg="#660000")
    loading_label.config(text="")


def blink_loading_dot():
    global blink_job, is_loading
    if not is_loading:
        blink_job = None
        return
    cur = loading_dot.cget("fg")
    loading_dot.config(fg="#660000" if cur == "#cc0000" else "#cc0000")
    blink_job = root.after(250, blink_loading_dot)


def trigger_action(event=None):
    if not searches_allowed:
        return
    if search_button["text"].startswith("Load More"):
        load_more_results()
    else:
        search_name()


def on_key_release(event):
    if event.keysym not in ("Return", "KP_Enter"):
        reset_to_search_mode()


# ───────────────────────────── Search / Display ─────────────────────────────
def search_name():
    global _search_id
    if not searches_allowed:
        return
    name_input = name_entry.get().strip()
    address_input = address_entry.get().strip()
    if not name_input and not address_input:
        # Also cancel any in-flight search so its results cannot appear later
        _search_id += 1
        stop_loading()
        clear_output()
        append_output("Please enter a name or an address.\n")
        clear_entry_selections()
        return

    # Bump the search id so any in-flight search becomes stale and aborts
    _search_id += 1
    my_id = _search_id

    clear_output()
    # Reset Deschutes batch state so a prior Load-More session cannot leak
    global all_matching_taxlots, taxlot_to_owner_map, displayed_results, total_results
    all_matching_taxlots = []
    taxlot_to_owner_map = {}
    displayed_results = 0
    total_results = 0

    county = get_current_county()
    state = state_var.get()
    start_loading("Countrywide" if state == COUNTRYWIDE else county)

    def still_current():
        return my_id == _search_id

    def do_search():
        global current_search_name, current_search_county, all_matching_taxlots
        global taxlot_to_owner_map, total_results, displayed_results
        _set_thread_qos(False)
        _user_work_enter()
        release = {"open": True}
        handed = False
        try:
            if not still_current():
                return
            current_search_county = "Countrywide" if state == COUNTRYWIDE else county

            # ── Countrywide search ────────────────────────────────────────
            if state == COUNTRYWIDE:
                all_results = []  # list of (state, county_name, feature, cfg)
                county_counts = {}
                missing_counties = []
                ready_counties = []
                overflow = False

                for st, counties in CONFIG.items():
                    for cname, cfg in counties.items():
                        if not still_current():
                            return
                        root.after(
                            0,
                            lambda c=cname: start_loading(c) if still_current() else None,
                        )
                        if not current_file_is_ready(st, cname):
                            missing_counties.append((st, cname))
                            continue
                        ready_counties.append((st, cname))
                        path = county_file_path(current_data_dir(), st, cname)
                        room = (
                            0 if len(all_results) >= RESULT_DISPLAY_CAP
                            else RESULT_DISPLAY_CAP - len(all_results)
                        )
                        try:
                            nmatch, features = query_county_match(
                                path, name_input, address_input, cfg, limit=room,
                            )
                        except Exception as exc:
                            raise CountyDownloadError(f"{st} {cname}: {exc}") from exc
                        if not still_current():
                            return
                        if nmatch:
                            county_counts[(st, cname)] = nmatch
                        for feature in features:
                            all_results.append((st, cname, feature, cfg))
                            if len(all_results) >= RESULT_DISPLAY_CAP:
                                break

                if not still_current():
                    return
                all_results, more = _cap_display_list(all_results)
                true_total = sum(county_counts.values())
                overflow = true_total > RESULT_DISPLAY_CAP or more
                n_unavail = sum(
                    1 for names in unavailableCounties.values() for c in names if c
                )
                n_zero = sum(1 for key in ready_counties if key not in county_counts)
                county_hits = [
                    (st, cname, county_counts[(st, cname)])
                    for st, counties in CONFIG.items()
                    for cname in counties
                    if (st, cname) in county_counts
                ]
                hit_states = len({st for st, _cname, _cnt in county_hits})
                total_results = len(all_results)
                displayed_results = 0

                def go_country(
                    results=all_results,
                    missing=len(missing_counties),
                    unavail=n_unavail,
                    zero=n_zero,
                    hits=county_hits,
                    states=hit_states,
                    hit_cap=overflow,
                    found=true_total,
                ):
                    finish_countrywide_search(
                        results, missing, unavail, zero, hits, states, my_id, release,
                        overflow=hit_cap, true_total=found,
                    )

                handed = True
                _release_user_work(release)
                try:
                    root.after(0, go_country)
                except Exception:
                    handed = False
                    raise
                return

            # ── Statewide search ──────────────────────────────────────────
            if county == "Statewide":
                county_names = list(CONFIG[state].keys())
                all_results = []          # list of (county_name, feature, cfg)
                county_counts = {}
                missing_counties = []
                stale_lines = []
                overflow = False
                searched_ready = []

                for cname in county_names:
                    if not still_current():
                        return
                    root.after(0, lambda c=cname: start_loading(c) if still_current() else None)
                    cfg = CONFIG[state][cname]
                    if not current_file_is_ready(state, cname):
                        missing_counties.append(cname)
                        continue
                    searched_ready.append(cname)
                    notice = stale_notice_line(state, cname)
                    if notice:
                        stale_lines.append(notice)
                    path = county_file_path(current_data_dir(), state, cname)
                    room = (
                        0 if len(all_results) >= RESULT_DISPLAY_CAP
                        else RESULT_DISPLAY_CAP - len(all_results)
                    )
                    try:
                        nmatch, features = query_county_match(
                            path, name_input, address_input, cfg, limit=room,
                        )
                    except Exception as exc:
                        raise CountyDownloadError(f"{cname}: {exc}") from exc
                    if not still_current():
                        return
                    if nmatch:
                        county_counts[cname] = nmatch
                    for feature in features:
                        all_results.append((cname, feature, cfg))
                        if len(all_results) >= RESULT_DISPLAY_CAP:
                            break

                if not still_current():
                    return
                all_results, more = _cap_display_list(all_results)
                true_total = sum(county_counts.values())
                overflow = true_total > RESULT_DISPLAY_CAP or more
                preface_lines = statewide_preface_lines(stale_lines, missing_counties)
                if not all_results and not preface_lines:
                    msg = "No matching properties found.\n"
                    root.after(0, lambda m=msg: (
                        _show_pinned_message(m, [], my_id),
                    ) if still_current() else None)
                    return
                if not all_results:
                    msg = "\n".join(preface_lines) + "\n"
                    root.after(0, lambda m=msg, notes=[]: (
                        _show_pinned_message(m, notes, my_id),
                    ) if still_current() else None)
                    return

                total_results = len(all_results)
                displayed_results = 0
                zero_names = [
                    c for c in searched_ready
                    if c not in county_counts
                ]

                def go_state(
                    results=all_results,
                    counts=county_counts,
                    names=county_names,
                    preface=preface_lines,
                    missing=missing_counties,
                    zeros=zero_names,
                    hit_cap=overflow,
                    found=true_total,
                ):
                    finish_statewide_search(
                        results, counts, names, my_id, preface, missing, release,
                        overflow=hit_cap, zero_counties=zeros, true_total=found,
                    )

                handed = True
                # The query is finished. Drawing must not keep the refresh paused.
                _release_user_work(release)
                try:
                    root.after(0, go_state)
                except Exception:
                    handed = False
                    raise
                return

            # ── Single-county search against the downloaded file ───────────
            cfg = get_cfg()
            if not current_file_is_ready(state, county):
                msg = offline_county_message(county)
                root.after(0, lambda m=msg: (
                    append_output(m),
                    stop_loading(my_id),
                ) if still_current() else None)
                return
            notice = stale_notice_line(state, county)
            path = county_file_path(current_data_dir(), state, county)
            nmatch, features = query_county_match(
                path, name_input, address_input, cfg, limit=RESULT_DISPLAY_CAP,
            )
            overflow = nmatch > RESULT_DISPLAY_CAP
            if not still_current():
                return
            features, more = _cap_display_list(features)
            overflow = overflow or more
            if not features:
                msg = ((notice + "\n\n") if notice else "") + "No matching properties found.\n"
                notes = [notice] if notice else []
                root.after(0, lambda m=msg, notes=notes: (
                    _show_pinned_message(m, notes, my_id),
                ) if still_current() else None)
                return
            total_results = len(features)
            displayed_results = 0

            def go_one(
                found=features, county_cfg=cfg, note=notice, hit_cap=overflow,
                n=nmatch,
            ):
                finish_normal_search(
                    found, county_cfg, my_id, note, release, overflow=hit_cap,
                    true_total=n,
                )

            handed = True
            # The query is finished. Drawing must not keep the refresh paused.
            _release_user_work(release)
            try:
                root.after(0, go_one)
            except Exception:
                handed = False
                raise
        except Exception as e:
            err = e
            if still_current():
                root.after(0, lambda err=err: (
                    append_output(f"Error: {err}\n"),
                    stop_loading(my_id),
                ) if still_current() else None)
        finally:
            if not handed:
                _release_user_work(release)

    threading.Thread(target=do_search, daemon=True).start()


_PAINT_BUDGET_S = 0.012
_PAINT_MAX_CARDS = 3
_PAINT_GAP_MS = 1


def _drop_cancelled_display(search_id):
    """Clear "Displaying results" when this paint lost to a county change."""
    if search_id is None or search_id == _search_id:
        return False
    try:
        if loading_label.cget("text") == "Displaying results":
            stop_loading()
    except (NameError, tk.TclError):
        pass
    return True


def _paint_result_chunks(search_id, items, paint_one, on_setup, on_chunk_done, release):
    """Draw a little, then return to the window so clicks stay responsive.

    The red loading dot stays up until the last card. The search query is
    already finished, so drawing does not keep the background refresh paused.
    """
    _release_user_work(release)
    shown = 0
    total = len(items)

    def step():
        nonlocal shown
        more = False
        try:
            if _drop_cancelled_display(search_id):
                return
            top = None
            try:
                if shown == 0 and on_setup is not None:
                    on_setup()
                    if _drop_cancelled_display(search_id):
                        return
                top = _snapshot_yview()
                deadline = time.perf_counter() + _PAINT_BUDGET_S
                painted = 0
                if shown < total:
                    output.config(state=tk.NORMAL)
                    try:
                        while shown < total and painted < _PAINT_MAX_CARDS:
                            if painted and time.perf_counter() >= deadline:
                                break
                            if search_id is not None and search_id != _search_id:
                                break
                            paint_one(items[shown], shown + 1)
                            shown += 1
                            painted += 1
                    finally:
                        output.config(state=tk.NORMAL)
                    if painted and on_chunk_done is not None and (search_id is None or search_id == _search_id):
                        on_chunk_done(shown)
            finally:
                if not _user_scrolled:
                    _restore_yview(top)
            if _drop_cancelled_display(search_id):
                return
            if shown < total:
                root.after(_PAINT_GAP_MS, step)
                more = True
                return
            search_button.config(text="Search", command=search_name)
            root.after(10, position_loading)
            stop_loading(search_id)
        except Exception as e:
            try:
                append_output(f"\nDisplay error: {e}\n")
                root.after(10, position_loading)
                stop_loading(search_id)
            except Exception:
                pass
        finally:
            if not more:
                _release_user_work(release)

    root.after(_PAINT_GAP_MS, step)


def finish_normal_search(
    features, cfg, search_id=None, notice=None, release=None, overflow=False,
    true_total=None,
):
    if search_id is not None and search_id != _search_id:
        _release_user_work(release)
        return

    def on_setup():
        global displayed_results, total_results, _pinned_result_notes
        if search_id is not None and search_id != _search_id:
            return
        total_results = len(features)
        displayed_results = 0
        extra = [notice] if notice else []
        _pinned_result_notes = _pin_overflow_notes(overflow, extra, total=true_total)
        update_header()
        start_loading_message("Displaying results")

    def on_chunk(shown):
        global displayed_results, total_results
        if search_id is not None and search_id != _search_id:
            return
        displayed_results = shown
        total_results = len(features)
        update_header()

    def paint_one(feature, number):
        display_result(feature, number, cfg, hold_state=True)

    try:
        _paint_result_chunks(search_id, features, paint_one, on_setup, on_chunk, release)
    except Exception:
        _release_user_work(release)
        raise


def finish_statewide_search(
    all_results, county_counts, all_county_names, search_id=None, preface_lines=None,
    missing_counties=None, release=None, overflow=False, zero_counties=None,
    true_total=None,
):
    """Display statewide results with the summary first, then cards in small batches."""
    if search_id is not None and search_id != _search_id:
        _release_user_work(release)
        return
    try:
        total = sum(county_counts.values()) if true_total is None else true_total
        skipped = set(missing_counties or [])
        if zero_counties is None:
            zero_counties = [
                c for c in all_county_names if c not in county_counts and c not in skipped
            ]
        state = state_var.get()
        unavail = [c for c in unavailableCounties.get(state, []) if c]
        lines = statewide_result_header_lines(
            preface_lines, unavail, zero_counties, county_counts, all_county_names, state,
            overflow=overflow, total=total,
        )
        header = "\n".join(lines) + "\n\n"

        def on_setup():
            global displayed_results, total_results, _pinned_result_notes
            total_results = len(all_results)
            displayed_results = 0
            # Do not pin last-updated lines. Restoring them on every header
            # rewrite moved them above the offline notices and added blanks.
            _pinned_result_notes = _pin_overflow_notes(overflow, total=total)
            output.config(state=tk.NORMAL)
            output.delete(1.0, tk.END)
            _link_urls.clear()
            output.insert("1.0", header)
            output.config(state=tk.NORMAL)
            if total_results:
                update_header()
            start_loading_message("Displaying results")

        def on_chunk(shown):
            global displayed_results, total_results
            if search_id is not None and search_id != _search_id:
                return
            displayed_results = shown
            total_results = len(all_results)
            update_header()

        def paint_one(item, number):
            cname, feature, cfg = item
            display_result(feature, number, cfg, county_label=cname, hold_state=True)

        _paint_result_chunks(search_id, all_results, paint_one, on_setup, on_chunk, release)
    except Exception as e:
        _release_user_work(release)
        append_output(f"\nDisplay error: {e}\n")
        root.after(10, position_loading)
        stop_loading(search_id)


def finish_countrywide_search(
    all_results, n_missing, n_unavail, n_zero, county_hits, hit_states,
    search_id=None, release=None, overflow=False, true_total=None,
):
    """Display countrywide results with compact counts, then cards."""
    if search_id is not None and search_id != _search_id:
        _release_user_work(release)
        return
    try:
        found = sum(count for _st, _c, count in county_hits) if true_total is None else true_total
        header = "\n".join(
            _overflow_header_lines(overflow, found)
            + countrywide_summary_lines(
                n_missing, n_unavail, n_zero, found, hit_states, county_hits,
            )
        ) + "\n\n"

        def on_setup():
            global displayed_results, total_results, _pinned_result_notes
            total_results = len(all_results)
            displayed_results = 0
            _pinned_result_notes = _pin_overflow_notes(overflow, total=found)
            output.config(state=tk.NORMAL)
            output.delete(1.0, tk.END)
            _link_urls.clear()
            output.insert("1.0", header)
            output.config(state=tk.NORMAL)
            if total_results:
                update_header()
            start_loading_message("Displaying results")

        def on_chunk(shown):
            global displayed_results, total_results
            if search_id is not None and search_id != _search_id:
                return
            displayed_results = shown
            total_results = len(all_results)
            update_header()

        def paint_one(item, number):
            st, cname, feature, cfg = item
            display_result(
                feature, number, cfg, county_label=cname, hold_state=True, result_state=st,
            )

        _paint_result_chunks(search_id, all_results, paint_one, on_setup, on_chunk, release)
    except Exception as e:
        _release_user_work(release)
        append_output(f"\nDisplay error: {e}\n")
        root.after(10, position_loading)
        stop_loading(search_id)


def show_next_batch(search_id=None):
    """Fetch the next batch of Deschutes details (network) then schedule all GUI
    updates on the main thread. Safe to call from a background thread."""
    global displayed_results
    if search_id is None:
        search_id = _search_id
    if search_id != _search_id:
        return
    cfg = get_cfg()
    start = displayed_results
    end = min(start + BATCH_SIZE, total_results)
    batch = all_matching_taxlots[start:end]

    prepared = []          # list of feature dicts ready for display_result
    failed_taxlots = []

    for taxlot in batch:
        if search_id != _search_id:
            return
        safe = str(taxlot).replace("'", "''")
        where = f"taxlot = '{safe}'"
        params = {
            "where": where,
            "outFields": "*",
            "returnGeometry": "false",
            "f": "pjson",
        }
        url = f"{cfg['base_url_details']}?{urlencode(params, quote_via=quote)}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _urlopen(req, timeout=12) as response:
                data = _parse_arcgis_json(response.read())
                details = data.get("features", [])
                for d in details:
                    owner = taxlot_to_owner_map.get(taxlot, "Unknown Owner")
                    if "," in owner and ", " not in owner:
                        owner = owner.replace(",", ", ")
                    d["attributes"]["name"] = owner
                    d["attributes"]["state"] = "OR"
                    prepared.append(d)
        except Exception as e:
            failed_taxlots.append((taxlot, str(e)))
        time.sleep(0.1)

    def update_gui():
        global displayed_results
        if search_id != _search_id:
            return
        for item in failed_taxlots:
            taxlot, err = item
            append_output(f"Warning: Failed for taxlot {taxlot} ({err})\n")
        for d in prepared:
            display_result(d, displayed_results + 1, cfg)
            displayed_results += 1
        update_header()
        if displayed_results < total_results:
            remaining = total_results - displayed_results
            search_button.config(
                text=f"Load More Results ({remaining} left)",
                command=load_more_results,
            )
        else:
            search_button.config(text="Search", command=search_name)
            append_output("\n--- All results loaded ---\n")
        root.after(10, position_loading)
        stop_loading(search_id)

    root.after(0, update_gui)


def load_more_results():
    my_id = _search_id  # stay on the same search; don't bump id
    start_loading(current_search_county)

    def do_load():
        _set_thread_qos(False)
        _user_work_enter()
        try:
            if my_id != _search_id:
                return
            show_next_batch(my_id)
        except Exception as e:
            if my_id == _search_id:
                root.after(0, lambda: append_output(f"Error: {e}"))
                root.after(0, lambda: stop_loading(my_id))
        finally:
            _user_work_leave()

    threading.Thread(target=do_load, daemon=True).start()


def _state_abbrev(val):
    if val is None:
        return None
    text = str(val).strip().upper()
    if not text:
        return None
    if len(text) == 2 and text.isalpha():
        return text
    text = text.replace(" STATE", "").strip()
    return _US_STATES.get(text)


def _norm_field_name(name):
    return (name or "").upper().replace(" ", "").replace("_", "")


def _field_kind(name):
    """situs, mail, or generic — from the field name, not the value."""
    u = _norm_field_name(name)
    if "MAIL" in u:
        return "mail"
    if "OWNER" in u and any(tok in u for tok in ("CITY", "ST", "ZIP", "ADDR", "PRV")):
        return "mail"
    if u.startswith(("MCITY", "MSTATE", "MZIP", "MADDRESS", "MADDR")):
        return "mail"
    if "SITUS" in u or "SITE" in u or u.startswith("EXPANDED"):
        return "situs"
    return "generic"


def _looks_city_name(name):
    u = _norm_field_name(name)
    if any(tok in u for tok in ("ZIP", "STATE", "STREET", "NUMBER")):
        return False
    if u.endswith("CITY") or u.endswith("CTY") or "COMMUNITY" in u:
        return True
    return "CITY" in u


def _looks_zip_name(name):
    return "ZIP" in _norm_field_name(name)


def _clean_field(val):
    if val is None:
        return None
    text = str(val).strip()
    return text if text else None


def _clean_zip(val):
    text = _clean_field(val)
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text.split("-")[0])
    if not digits or int(digits) == 0:
        return None
    return text


def _city_in_street(city, street):
    if not city or not street:
        return False
    return re.search(rf"\b{re.escape(str(city).strip())}\b", str(street), re.I) is not None


def _split_csz(val):
    """Pull city / ST / zip out of a combined CSZ string."""
    text = _clean_field(val)
    if not text:
        return None, None, None
    match = re.match(
        r"^(?P<city>[A-Za-z][A-Za-z .'-]*?)\s*,\s*(?P<st>[A-Za-z]{2})"
        r"\s*,?\s*(?P<zip>\d{5}(?:-\d{4})?)?\s*$",
        text,
    )
    if match and match.group("st").upper() in _US_ABBREVS:
        return (
            match.group("city").strip(" ,"),
            match.group("st").upper(),
            match.group("zip"),
        )
    return text, None, None


def _parse_situs_blob(text):
    """City/state/zip already sitting on the situs street line."""
    raw = _clean_field(text)
    if not raw:
        return None
    match = re.match(
        r"^(?P<street>.+?)\s+(?P<city>[A-Za-z][A-Za-z .'-]*?)\s+"
        r"(?P<st>[A-Za-z]{2})\s+(?P<zip>\d{5}(?:-\d{4})?)\s*$",
        raw,
    )
    if match and match.group("st").upper() in _US_ABBREVS:
        return {
            "street": match.group("street").strip(" ,"),
            "city": match.group("city").strip(" ,"),
            "state": match.group("st").upper(),
            "zip": match.group("zip"),
        }
    match = re.match(
        r"^(?P<street>.+),\s*(?P<city>[A-Za-z][A-Za-z .'-]+)\s*$",
        raw,
    )
    if match:
        city = match.group("city").strip(" ,")
        if len(city) >= 3:
            return {
                "street": match.group("street").strip(" ,"),
                "city": city,
                "state": None,
                "zip": None,
            }
    return None


def resolve_display_location(attr, cfg, display_addr, addr_val, is_mailing, home_state):
    """City/state/zip that belong with the displayed street.

    Situs streets are never paired with a mailing city (Jackson SITEADD +
    CITY Tucson AZ). When the situs city is unknown, the city is Unincorporated.
    """
    home_ab = _state_abbrev(home_state) or _clean_field(home_state)
    if home_ab:
        home_ab = home_ab.upper() if len(str(home_ab)) == 2 else (
            _state_abbrev(home_ab) or home_ab
        )

    def take(field):
        return _clean_field(attr.get(field)) if field else None

    def take_zip(field):
        return _clean_zip(attr.get(field)) if field else None

    if is_mailing:
        city = take("EXPANDED_CITY")
        used_city = "EXPANDED_CITY" if city else None
        if not city and cfg.get("fallback_city_field"):
            city = take(cfg["fallback_city_field"])
            if city:
                used_city = cfg["fallback_city_field"]
        if not city and cfg.get("city_field"):
            city = take(cfg["city_field"])
            if city:
                used_city = cfg["city_field"]
        if city:
            csz_city, _csz_st, csz_zip = _split_csz(city)
            if csz_city:
                city = csz_city
        else:
            csz_zip = None
        zip_val = take("EXPANDED_ZIP")
        used_zip = "EXPANDED_ZIP" if zip_val else None
        if not zip_val and csz_zip:
            zip_val = csz_zip
            used_zip = used_city
        if not zip_val and cfg.get("fallback_zip_field"):
            zip_val = take_zip(cfg["fallback_zip_field"])
            if zip_val:
                used_zip = cfg["fallback_zip_field"]
        if not zip_val and cfg.get("zip_field"):
            zip_val = take_zip(cfg["zip_field"])
            if zip_val:
                used_zip = cfg["zip_field"]
        state_val = take("EXPANDED_STATE")
        showed_db_state = bool(state_val)
        if not state_val and cfg.get("state_field"):
            state_val = take(cfg["state_field"])
            showed_db_state = bool(state_val)
        if not state_val:
            state_val = home_ab
            showed_db_state = False
        return city, state_val, zip_val, used_city, used_zip, showed_db_state, False

    city = take("EXPANDED_CITY")
    used_city = "EXPANDED_CITY" if city else None
    zip_val = take("EXPANDED_ZIP")
    used_zip = "EXPANDED_ZIP" if zip_val else None

    if not city:
        for key, val in attr.items():
            if _field_kind(key) != "situs" or not _looks_city_name(key):
                continue
            cand = _clean_field(val)
            if not cand:
                continue
            csz_city, _csz_st, csz_zip = _split_csz(cand)
            city = csz_city or cand
            used_city = key
            if csz_zip and not zip_val:
                zip_val = csz_zip
                used_zip = key
            break

    if not city:
        city_field = cfg.get("city_field")
        kind = _field_kind(city_field) if city_field else None
        cand = take(city_field)
        if cand and kind == "situs":
            csz_city, _csz_st, csz_zip = _split_csz(cand)
            city = csz_city or cand
            used_city = city_field
            if csz_zip and not zip_val:
                zip_val = csz_zip
                used_zip = city_field
        elif cand and kind == "generic":
            st_field = cfg.get("state_field")
            st_val = _state_abbrev(take(st_field)) if st_field else None
            if st_field and _field_kind(st_field) == "mail":
                st_val = None
            out_of_state = bool(st_val and home_ab and st_val != home_ab)
            if not out_of_state and _city_in_street(cand, addr_val):
                city = cand
                used_city = city_field
                zip_field = cfg.get("zip_field")
                if not zip_val and zip_field and _field_kind(zip_field) != "mail":
                    zip_val = take_zip(zip_field)
                    if zip_val:
                        used_zip = zip_field

    if not city:
        parsed = _parse_situs_blob(addr_val)
        if parsed and parsed.get("city"):
            p_st = parsed.get("state")
            if not p_st or not home_ab or p_st == home_ab:
                city = parsed["city"]
                used_city = None
                if parsed.get("zip") and not zip_val:
                    zip_val = parsed["zip"]

    if not zip_val:
        zip_field = cfg.get("zip_field")
        if zip_field and _field_kind(zip_field) == "situs":
            zip_val = take_zip(zip_field)
            if zip_val:
                used_zip = zip_field
        if not zip_val:
            for key, val in attr.items():
                if _field_kind(key) != "situs" or not _looks_zip_name(key):
                    continue
                zval = _clean_zip(val)
                if zval:
                    zip_val = zval
                    used_zip = key
                    break

    unincorporated = not bool(city)
    if unincorporated:
        city = UNINCORPORATED_CITY
        used_city = None
        zip_val = None
        used_zip = None
    return city, home_ab, zip_val, used_city, used_zip, False, unincorporated


def _maps_location_query(street, city_val, state_val, zip_val, unincorporated, county_name):
    loc_parts = []
    if street:
        loc_parts.append(street)
    if unincorporated:
        if county_name:
            loc_parts.append(county_name)
    elif city_val:
        loc_parts.append(city_val)
    if state_val and zip_val and not unincorporated:
        loc_parts.append(f"{state_val} {zip_val}")
    elif state_val:
        loc_parts.append(state_val)
    elif zip_val and not unincorporated:
        loc_parts.append(str(zip_val))
    return ", ".join(loc_parts)


def display_result(feature, index, cfg, county_label=None, hold_state=False, result_state=None):
    if not hold_state:
        output.config(state=tk.NORMAL)
    try:
        attr = feature.get("attributes", {})
        output.insert(tk.END, f"─── Result {index} ───\n")
        if result_state:
            output.insert(tk.END, f"State: {result_state}\n")
        if county_label:
            output.insert(tk.END, f"County: {county_label}\n")

        owner_fields = cfg.get("owner_field")
        if isinstance(owner_fields, list):
            owners = [attr.get(f) for f in owner_fields if attr.get(f)]
            if owners:
                output.insert(tk.END, f"Owner: {' / '.join(owners)}\n")
        else:
            val = attr.get(owner_fields)
            if val:
                output.insert(tk.END, f"Owner: {val}\n")

        home_source = result_state if result_state else state_var.get()

        display_addr = cfg.get("display_address_field") or cfg.get("address_field")
        addr_val = _clean_field(attr.get(display_addr))
        used_fallback_address = False
        if not addr_val and attr.get("EXPANDED_ADDRESS"):
            display_addr = "SITEADD"
            addr_val = _clean_field(attr.get("EXPANDED_ADDRESS"))
        if not addr_val and cfg.get("fallback_address_field"):
            fallback = _clean_field(attr.get(cfg["fallback_address_field"]))
            if fallback:
                addr_val = fallback
                display_addr = cfg["fallback_address_field"]
                used_fallback_address = True

        is_mailing_address = used_fallback_address or ("MAIL" in (display_addr or "").upper())

        if addr_val:
            output.insert(tk.END, f"{display_addr}: {addr_val}\n")

        (
            city_val, state_val, zip_val, used_city_field, used_zip_field,
            showed_db_state, city_unincorporated,
        ) = resolve_display_location(
            attr, cfg, display_addr, addr_val, is_mailing_address, home_source,
        )

        if city_val:
            output.insert(tk.END, f"City: {city_val}\n")
        if state_val:
            output.insert(tk.END, f"State: {state_val}\n")
        if zip_val:
            output.insert(tk.END, f"Zip: {zip_val}\n")

        if addr_val:
            # Build a structured "street, city, ST zip" query for reliable geocoding.
            # Some counties (e.g. Gilliam) store the full address in one field with no
            # commas ("51748 RAMSAY CANYON RD CONDON OR 97823").  Google often fails
            # on that blob and only matches the road name.  Strip trailing city /
            # state / zip out of the street portion, then reassemble with commas.
            street = addr_val.strip()
            if city_val:
                idx = street.upper().rfind(city_val.upper())
                if idx > 0:
                    street = street[:idx].strip(" ,")
            if state_val:
                street = re.sub(
                    rf"\b{re.escape(state_val)}\b\s*$", "", street, flags=re.IGNORECASE
                ).strip(" ,")
            if zip_val:
                street = re.sub(
                    rf"\b{re.escape(str(zip_val))}\b\s*$", "", street, flags=re.IGNORECASE
                ).strip(" ,")
            # Collapse leftover double spaces
            street = re.sub(r"\s{2,}", " ", street).strip(" ,")

            map_county = county_label
            if not map_county:
                cname = current_search_county
                if cname and cname not in ("Statewide", COUNTRYWIDE):
                    map_county = cname
            loc_query = _maps_location_query(
                street, city_val, state_val, zip_val, city_unincorporated, map_county,
            ) or addr_val

            # Address is stored on the tag. The URL is built at click time from
            # the Map Link Type dropdown so changing it does not need a re-search.
            _insert_clickable_text("🔗 Open in Maps", ("maps", loc_query), f"maps_{index}")

            zillow_slug = loc_query.replace(",", "").replace("  ", " ").strip().replace(" ", "-")
            zillow = f"https://www.zillow.com/homes/{zillow_slug}_rb/"
            _insert_clickable_text("🔗 Open in Zillow", zillow, f"zillow_{index}")

        if not _clean_view_on():
            if attr.get("taxlot"):
                output.insert(tk.END, f"taxlot: {attr['taxlot']}\n")

            if not addr_val:
                if attr.get("subdivision"):
                    output.insert(tk.END, f"subdivision: {attr['subdivision']}\n")
                if attr.get("lot"):
                    output.insert(tk.END, f"lot: {attr['lot']}\n")

            skip = set()
            of = cfg.get("owner_field")
            skip.update(of if isinstance(of, list) else [of])
            skip.update([
                display_addr,
                cfg.get("display_csz_field"),
                "EXPANDED_ADDRESS", "EXPANDED_CITY", "EXPANDED_STATE", "EXPANDED_ZIP",
                "taxlot", "subdivision", "lot",
            ])
            # Only hide the specific fields whose values were shown at the top
            if used_city_field:
                skip.add(used_city_field)
            if used_zip_field:
                skip.add(used_zip_field)
            if showed_db_state and cfg.get("state_field"):
                skip.add(cfg["state_field"])
            if not is_mailing_address:
                for fld in (cfg.get("city_field"), cfg.get("zip_field"), cfg.get("state_field")):
                    if fld and fld not in (used_city_field, used_zip_field):
                        skip.add(fld)
            # Remove None that can sneak in from missing optional keys
            skip.discard(None)

            for key, value in attr.items():
                if key in skip or value is None or not str(value).strip():
                    continue
                if isinstance(value, (int, float)) and key not in ("OBJECTID", "ACCOUNT", "TAXLOT", "ZIPCODE", "ZIP"):
                    value = f"{value:,}"
                output.insert(tk.END, f"{key}: {value}\n")

        output.insert(tk.END, "-" * 60 + "\n\n")
    finally:
        if not hold_state:
            output.config(state=tk.NORMAL)


# ───────────────────────────── Download / update services ─────────────────────
def set_searches_allowed(allowed):
    global searches_allowed, _search_id
    searches_allowed = bool(allowed)
    if not searches_allowed:
        _search_id += 1
        if not _holding_download_status:
            stop_loading()
    try:
        search_button.config(state=tk.NORMAL if searches_allowed else tk.DISABLED)
    except (NameError, tk.TclError):
        pass


# Arial 18's bullet sits low in its line box. Geometric centering puts the
# 10pt status text about 3px above the bullet's ink.
_GOLD_TEXT_NUDGE = 3
_GOLD_DOT_X = -1
_DROPDOWN_Y_NUDGE = 3
_CHECKBOX_Y_NUDGE = 2


def align_gold_text(event=None):
    """Line the gold status text up with the bullet, using their real sizes."""
    if getattr(align_gold_text, "busy", False):
        return
    align_gold_text.busy = True
    try:
        try:
            gold_dot.update_idletasks()
            dot_h = gold_dot.winfo_height()
            dot_w = gold_dot.winfo_width()
            if dot_h <= 1:
                dot_h = gold_dot.winfo_reqheight()
            if dot_w <= 1:
                dot_w = gold_dot.winfo_reqwidth()
            lab_h = gold_label.winfo_reqheight()
            # The bullet's ink sits low in the 18pt box. Drop the 10pt line
            # so the two inks share a center. Wrapped text stays top-aligned.
            nudge = _GOLD_TEXT_NUDGE if lab_h <= dot_h else 0
            y_guess = (dot_h - lab_h) // 2 + nudge
            if y_guess < 0:
                y_guess = 0
            row_h = max(dot_h, y_guess + lab_h)
            gold_row.pack_propagate(False)
            gold_row.configure(height=row_h)
            gold_row.update_idletasks()
            dot_y = gold_dot.winfo_y()
            live_h = gold_dot.winfo_height()
            if live_h > 1:
                dot_h = live_h
            else:
                dot_y = 0
            y = dot_y + (dot_h - lab_h) // 2 + nudge
            if y < 0:
                y = 0
            if y + lab_h > gold_row.winfo_height():
                gold_row.configure(height=y + lab_h)
            gold_dot.place(x=_GOLD_DOT_X, y=dot_y if live_h > 1 else 0)
            x = _GOLD_DOT_X + max(dot_w, gold_dot.winfo_width()) + 4
            gold_label.place(x=x, y=y, relwidth=1.0, width=-x)
        except (NameError, tk.TclError):
            pass
    finally:
        align_gold_text.busy = False


def fit_status_wrap(event=None):
    try:
        width = max(root.winfo_width() - 80, 240)
        gold_label.config(wraplength=width)
        update_prompt.config(wraplength=width)
        county_update_line.config(wraplength=width)
    except (NameError, tk.TclError):
        pass
    align_gold_text()
    choice = _open_choice
    if choice is not None:
        choice.place()


def _clear_idle_gold():
    """Drop the grabbing message when this launch has nothing left to refresh."""
    if _gold_blinking and gold_label.cget("text").startswith("Grabbing most recent data"):
        stop_gold_blink("")
        gold_dot.config(fg="#6e5600")


def _grabbing_update_text(county):
    return (
        f"Grabbing most recent data from online taxlots for "
        f"{_county_sentence_name(county)} county."
    )


def _show_grabbing_gold(county):
    global _refreshing_county
    _refreshing_county = county
    start_gold(_grabbing_update_text(county))


def start_gold(text):
    global _gold_blinking, _gold_blink_job, _gold_bright
    gold_label.config(text=text)
    fit_status_wrap()
    if _gold_blinking:
        return
    _gold_blinking = True
    _gold_bright = False
    if _gold_blink_job:
        try:
            root.after_cancel(_gold_blink_job)
        except Exception:
            pass
        _gold_blink_job = None
    blink_gold()


def stop_gold_blink(text):
    global _gold_blinking, _gold_blink_job, _gold_bright
    _gold_blinking = False
    _gold_bright = True
    if _gold_blink_job:
        try:
            root.after_cancel(_gold_blink_job)
        except Exception:
            pass
        _gold_blink_job = None
    gold_dot.config(fg="#e1b000")
    gold_label.config(text=text)
    fit_status_wrap()


def _pause_gold_for_auto_update():
    """Dim the refresh dot while the checkbox is holding the download."""
    stop_gold_blink("Auto update paused.")
    try:
        gold_dot.config(fg="#6e5600")
    except (NameError, tk.TclError):
        pass


def blink_gold():
    global _gold_blink_job, _gold_bright
    if not _gold_blinking:
        _gold_blink_job = None
        return
    _gold_bright = not _gold_bright
    gold_dot.config(fg="#e1b000" if _gold_bright else "#6e5600")
    _gold_blink_job = root.after(250, blink_gold)


_UPDATED_RECORDS_TEXT = "Taxlot records updated to most recent data."


def show_finished_updating():
    """Leave the finished sentence on screen. It does not expire."""
    global _finish_seconds_left, _finish_timer
    _finish_seconds_left = 0
    if _finish_timer is not None:
        try:
            root.after_cancel(_finish_timer)
        except Exception:
            pass
        _finish_timer = None
    update_prompt.config(text=_UPDATED_RECORDS_TEXT)
    if not update_prompt.winfo_ismapped():
        update_prompt.pack(anchor="w", pady=(2, 0))
    fit_status_wrap()


def show_county_up_to_date(county):
    """Show "{name} county up to date" for 5 seconds.

    The countdown starts immediately. It keeps running when the window is in
    the background, and a newer county replaces the line and starts its own
    5 seconds.
    """
    global _county_line_job, _county_line_token
    _county_line_token += 1
    token = _county_line_token
    name = _county_sentence_name(county)
    try:
        county_update_line.config(text=f"{name} county up to date")
    except (NameError, tk.TclError):
        return
    if _county_line_job is not None:
        try:
            root.after_cancel(_county_line_job)
        except Exception:
            pass

    def clear(token=token):
        global _county_line_job
        if token != _county_line_token:
            return
        _county_line_job = None
        try:
            county_update_line.config(text="")
        except (NameError, tk.TclError):
            pass

    _county_line_job = root.after(5000, clear)


def on_app_focus_change(event=None):
    return


def initial_download(missing):
    for state, county in missing:
        place = _county_in_state(state, county)
        root.after(0, lambda p=place: start_loading_message(f"Downloading {p}"))
        path = county_file_path(current_data_dir(), state, county)
        try:
            download_county_file(CONFIG[state][county], path, polite=False)
        except Exception:
            _counties_download_failed.add((state, county))
            leave_download_unfinished(path)
            root.after(0, lambda c=county, st=state: append_output(offline_county_message(c, st)))

    def finished():
        global _holding_download_status
        _holding_download_status = False
        stop_loading()
        set_searches_allowed(True)
        note_offline_selection()
        # First-time downloads are finished. One refresh pass may start now.
        _begin_session_refresh()

    root.after(0, finished)


def _auto_update_pref_path():
    return _app_support_dir() / "auto_update.txt"


def _read_auto_update_pref():
    """The checkbox is off until the user turns it on and that choice is saved."""
    try:
        return _auto_update_pref_path().read_text(encoding="utf-8").strip() == "1"
    except OSError:
        return False


def _write_auto_update_pref(enabled):
    path = _auto_update_pref_path()
    tmp = path.with_name(path.name + ".replacing")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("1\n" if enabled else "0\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def on_auto_update_toggle():
    """Pause or resume this launch's refresh. The choice is kept for next time.

    Turning it off does not delete the update file. Closing the app does that
    on the next launch, the same way any unfinished download is removed.
    Turning it back on here wakes the waiting download thread.
    """
    enabled = bool(auto_update_var.get())
    _write_auto_update_pref(enabled)
    if enabled:
        _auto_update_on.set()
        if _refresh_active:
            if _refreshing_county:
                start_gold(_grabbing_update_text(_refreshing_county))
            else:
                start_gold("Grabbing most recent data from online taxlots.")
            return
        # Startup deletes unfinished update files before a refresh may start.
        # A pass that already finished stays finished. First-time downloads
        # call _begin_session_refresh when they are done.
        if not _startup_ready or _holding_download_status or _refresh_started:
            return
        _begin_session_refresh()
        return
    _auto_update_on.clear()
    if _refresh_active:
        _pause_gold_for_auto_update()


def _begin_session_refresh():
    """Start the one refresh pass for this launch, after startup loading.

    The pass stays idle while the auto-update checkbox is off. Turning the
    box on later in this launch starts it then.
    """
    global _refresh_started, _refresh_active, schedule
    if _refresh_started:
        return
    if not _auto_update_on.is_set():
        return
    due = list(refresh_candidates())
    # This launch's order. The next launch builds a new list from the files on disk.
    schedule = due
    if not due:
        return
    _refresh_started = True
    _refresh_active = True
    for pair in due:
        _session_refresh_claimed.add(pair)
    _show_grabbing_gold(due[0][1])
    threading.Thread(target=lambda due=list(schedule): background_update(due), daemon=True).start()


def background_update(due):
    """Update one scheduled county fully, then move to the next.

    `due` is this session's schedule, oldest current file first. The list is
    fixed when the pass starts. A later clock reading cannot add counties or
    start another pass. Each finished update file replaces that county before
    the next download starts.
    """
    _set_thread_qos(True)
    due = list(due)
    if not due:
        root.after(0, _clear_idle_gold)
        root.after(0, _mark_refresh_inactive)
        return
    global _refreshing_county
    failed = []
    succeeded = []
    swapped = 0
    for state, county in due:
        # Between counties, wait before opening the next update file.
        _wait_for_auto_update()
        if _current_file_is_from_this_session(state, county):
            succeeded.append((state, county))
            continue
        _refreshing_county = county
        root.after(0, lambda c=county: start_gold(_grabbing_update_text(c)))
        path = county_file_path(update_data_dir(), state, county)
        try:
            download_county_file(CONFIG[state][county], path, polite=True)
            if not _replace_one_county(state, county):
                failed.append((state, county))
                continue
        except Exception:
            failed.append((state, county))
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
            continue
        succeeded.append((state, county))
        swapped += 1
        root.after(0, lambda county=county: show_county_up_to_date(county))

    def finish():
        global session_failed_counties
        _mark_refresh_inactive()
        session_failed_counties = set(failed)
        missed = len(due) - len(succeeded)
        if missed:
            when = format_updated_for_user(_now_stamp())
            noun = "county" if missed == 1 else "counties"
            stop_gold_blink(
                f"All except {missed} {noun} up to date as of {when}. "
                "Counties that were not updated in this session will notify you before displaying results."
            )
            return
        session_failed_counties = set()
        _feature_cache.clear()
        _feature_cache_order.clear()
        stop_gold_blink("All downloaded taxlots up to date.")
        # The sentence stays only after a current file was actually swapped.
        if swapped:
            show_finished_updating()

    root.after(0, finish)


def _mark_refresh_inactive():
    global _refresh_active, _refreshing_county
    _refresh_active = False
    _refreshing_county = None


def _replace_one_county(state, county):
    """Swap one finished update file over the current file.

    The results window is left as it is. Nothing is created when this county
    has no current file. The current file stays searchable until the swap.
    """
    src = county_file_path(update_data_dir(), state, county)
    dest = county_file_path(current_data_dir(), state, county)
    if not src.is_file() or not dest.is_file():
        return False
    header = peek_county_header(src)
    if not header or not header[1]:
        try:
            src.unlink()
        except OSError:
            pass
        return False
    try:
        con = _connect_county_db(src, readonly=True)
        try:
            row = con.execute("SELECT COUNT(*) FROM parcels").fetchone()
            nparcels = int(row[0] if row else 0)
        finally:
            con.close()
    except sqlite3.Error:
        nparcels = 0
    if nparcels < 1:
        try:
            src.unlink()
        except OSError:
            pass
        return False
    _copy_data_file(src, dest)
    try:
        src.unlink()
    except OSError:
        pass
    return True


def _finish_startup_check():
    global _holding_download_status, _startup_ready
    _startup_ready = True
    missing = [
        (state, county)
        for state, county in iter_implemented_counties()
        if not current_file_is_ready(state, county)
    ]
    if missing:
        _holding_download_status = True
        set_searches_allowed(False)
        threading.Thread(target=lambda: initial_download(missing), daemon=True).start()
        return
    stop_loading()
    set_searches_allowed(True)
    note_offline_selection()
    _begin_session_refresh()
    if _refresh_started:
        return
    try:
        if not _gold_blinking:
            gold_label.config(text="")
    except (NameError, tk.TclError):
        pass


def begin_data_services():
    """Clear bad files, adopt finished .py county files, then allow search.

    Adoption reads the old JSON files off the main thread. A county that
    already has a finished SQLite file is left as it is.
    """
    set_searches_allowed(False)
    prepare_storage_access()
    current_data_dir().mkdir(parents=True, exist_ok=True)

    def prepare():
        _set_thread_qos(True)
        try:
            bundle = _bundle_resources_dir()
            if bundle is not None:
                try:
                    migrate_bundled_taxlots(bundle, _app_support_dir())
                except OSError:
                    pass
            delete_unfinished_files(current_data_dir())
            clear_directory(update_data_dir())
            for state, county in iter_implemented_counties():
                if current_file_is_ready(state, county):
                    continue
                legacy = county_file_path(current_data_dir(), state, county).with_suffix(".py")
                header = _peek_py_header(legacy) if legacy.is_file() else None
                if not header or not header[1]:
                    continue
                try:
                    root.after(0, lambda st=state, c=county: _show_prepare_status(st, c))
                except tk.TclError:
                    return
                try:
                    adopt_legacy_county_file(state, county)
                except Exception:
                    pass
        finally:
            try:
                root.after(0, _finish_startup_check)
            except tk.TclError:
                pass

    threading.Thread(target=prepare, daemon=True).start()


def _show_prepare_status(state, county):
    try:
        start_loading_message(
            f"Preparing {_county_in_state(state, county)} for search."
        )
    except (NameError, tk.TclError):
        pass


class ChoiceMenu:
    """Dropdown that stays open while results are drawn.

    A native macOS menu closes when the results window changes. This list is
    its own window, with the same hover, current-item, and type-ahead behavior.
    """

    _HOVER_BG = "#d4e6f8"
    _HOVER_FG = "#000000"
    _SELECTED_BG = "#0a6bcb"
    _SELECTED_FG = "#ffffff"
    _TYPE_TIMEOUT = 0.8

    def __init__(self, parent, variable, values, width):
        self.variable = variable
        self.values = list(values)
        self._min_width = int(width)
        # Keep this pixel/character width for the life of the control so
        # Countrywide's empty county list does not shrink the row.
        self._button_width = self._width_for(self.values)
        self.button = tk.Menubutton(
            parent,
            width=self._button_width,
            font=("Arial", 12),
            anchor="w",
            indicatoron=True,
            takefocus=0,
        )
        self.panel = None
        self.listbox = None
        self._spot = None
        self._placing = False
        self._reveal = False
        self._hover = None
        self._cursor = None
        self._from_keys = False
        self._typed = ""
        self._typed_at = 0.0
        self._focus_before = None
        self._keyed = []
        self._sync_button()
        self.variable.trace_add("write", self._sync_button)
        self.button.bind("<Button-1>", self._on_press)
        self.button.bind("<ButtonRelease-1>", lambda _event: "break")

    def _width_for(self, values):
        longest = max((len(value) for value in values), default=0)
        return max(self._min_width, longest)

    def _sync_button(self, *_):
        try:
            text = self.variable.get()
            # A native Aqua menubutton with "" collapses; a space keeps the size.
            self.button.config(text=text if text else " ")
        except tk.TclError:
            pass

    def pack(self, **kwargs):
        self.button.pack(**kwargs)

    def set_values(self, values):
        self.values = list(values)
        self.button.config(width=self._button_width)
        if not self.values:
            self.close()
            return
        if self.listbox is not None:
            self._fill()
            self._spot = None
            self.place()

    def _on_press(self, event):
        self.toggle()
        return "break"

    def toggle(self):
        if self.panel is not None:
            self.close()
        else:
            self.open()

    def open(self):
        global _open_choice
        if not self.values:
            return
        if self.panel is not None:
            return
        if _open_choice is not None and _open_choice is not self:
            _open_choice.close()
        try:
            self._focus_before = root.focus_get()
        except tk.TclError:
            self._focus_before = None
        panel = tk.Toplevel(root)
        # Stay invisible until place() has the final screen position. Mapping
        # first paints the window at Tk's default spot, and macOS then slides
        # it into place.
        panel.withdraw()
        try:
            panel.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        panel.overrideredirect(True)
        # A help window does not become the key window, so closing it does not
        # stop the name and address fields from taking clicks.
        try:
            root.tk.call(
                "::tk::unsupported::MacWindowStyle", "style", panel,
                "help", "noActivates",
            )
        except tk.TclError:
            pass
        panel.withdraw()
        try:
            panel.attributes("-topmost", True)
        except tk.TclError:
            pass
        panel.configure(bg="#8a8a8a")
        height = min(max(len(self.values), 1), 16)
        listbox = tk.Listbox(
            panel,
            font=("Arial", 12),
            height=height,
            activestyle="none",
            exportselection=False,
            takefocus=0,
            borderwidth=0,
            highlightthickness=0,
            selectbackground=self._HOVER_BG,
            selectforeground=self._HOVER_FG,
            width=max(self._width_for(self.values), 12),
        )
        if len(self.values) > height:
            scroll = tk.Scrollbar(panel, orient="vertical", command=listbox.yview)
            listbox.config(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y", padx=(0, 1), pady=1)
        listbox.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        self.panel = panel
        self.listbox = listbox
        self._cursor = self._selected_index()
        self._hover = None
        self._from_keys = False
        self._typed = ""
        self._fill()
        _open_choice = self
        self._spot = None
        self._reveal = True
        self.place()
        self._sync_hover_from_pointer()
        listbox.bind("<Motion>", self._on_motion)
        listbox.bind("<Leave>", self._on_leave)
        listbox.bind("<Button-1>", self._on_row_press)
        listbox.bind("<ButtonRelease-1>", self._on_row_release)
        listbox.bind("<MouseWheel>", self._on_wheel)
        try:
            self.button.config(state="active")
        except tk.TclError:
            pass
        self._enable_keys()

    def _fill(self):
        self.listbox.delete(0, tk.END)
        for value in self.values:
            self.listbox.insert(tk.END, value)
        self._paint_rows()
        index = self._pointed_index()
        if index is None:
            index = self._selected_index()
        if index is not None:
            self.listbox.see(index)

    def _selected_index(self):
        try:
            return self.values.index(self.variable.get())
        except ValueError:
            return None

    def _paint_rows(self):
        listbox = self.listbox
        if listbox is None:
            return
        selected = self._selected_index()
        pointed = self._pointed_index()
        try:
            listbox.selection_clear(0, tk.END)
        except tk.TclError:
            return
        for index in range(len(self.values)):
            # The current value stays brighter, even under the pointer.
            if index == selected:
                bg, fg = self._SELECTED_BG, self._SELECTED_FG
            elif index == pointed:
                bg, fg = self._HOVER_BG, self._HOVER_FG
            else:
                bg, fg = "", ""
            listbox.itemconfig(index, background=bg, foreground=fg)

    def _pointed_index(self):
        """Row to mark: the keyboard row after a key, otherwise the mouse row."""
        if self._from_keys:
            return self._cursor
        if self._hover is not None:
            return self._hover
        return self._cursor

    def _index_at(self, event):
        listbox = self.listbox
        if listbox is None:
            return None
        try:
            index = listbox.nearest(event.y)
            box = listbox.bbox(index)
        except tk.TclError:
            return None
        if box is None or event.y < box[1] or event.y >= box[1] + box[3]:
            return None
        if index < 0 or index >= len(self.values):
            return None
        return index

    def _show_index(self, index):
        self._from_keys = True
        self._cursor = index
        self._paint_rows()
        if self.listbox is not None and index is not None:
            try:
                self.listbox.see(index)
            except tk.TclError:
                pass

    def _on_motion(self, event):
        index = self._index_at(event)
        if index == self._hover and not self._from_keys:
            return
        self._hover = index
        self._from_keys = False
        self._paint_rows()

    def _on_leave(self, _event):
        if self._hover is None:
            return
        self._hover = None
        self._paint_rows()

    def _on_row_press(self, event):
        index = self._index_at(event)
        if index is not None:
            self._hover = index
            self._from_keys = False
            self._paint_rows()
        return "break"

    def _sync_hover_from_pointer(self):
        """Mark the row under the pointer as soon as the list opens."""
        listbox = self.listbox
        if listbox is None:
            return
        try:
            listbox.update_idletasks()
            px, py = listbox.winfo_pointerxy()
            lx, ly = listbox.winfo_rootx(), listbox.winfo_rooty()
            width, height = listbox.winfo_width(), listbox.winfo_height()
        except tk.TclError:
            return
        if width <= 1 or height <= 1:
            return
        if not (lx <= px < lx + width and ly <= py < ly + height):
            return
        index = self._index_at(type("Ev", (), {"y": py - ly})())
        if index is None:
            return
        self._hover = index
        self._from_keys = False
        self._paint_rows()

    def _on_row_release(self, event):
        index = self._index_at(event)
        if index is None:
            return "break"
        self._choose_index(index)
        return "break"

    def _on_wheel(self, event):
        if self.listbox is None:
            return "break"
        step = -1 if event.delta > 0 else 1
        self.listbox.yview_scroll(step, "units")
        return "break"

    def _choose_index(self, index):
        if index is None or index < 0 or index >= len(self.values):
            self.close()
            return
        value = self.values[index]
        self.close()
        if self.variable.get() != value:
            self.variable.set(value)

    def _on_key(self, event):
        if _open_choice is not self or self.panel is None:
            return
        key = event.keysym
        if key == "Escape":
            self.close()
            return "break"
        if key in ("Return", "KP_Enter"):
            index = self._pointed_index()
            if index is None:
                index = self._selected_index()
            self._choose_index(index)
            return "break"
        if key == "Down":
            self._move(1)
            return "break"
        if key == "Up":
            self._move(-1)
            return "break"
        if key == "Home":
            if self.values:
                self._show_index(0)
            return "break"
        if key == "End":
            if self.values:
                self._show_index(len(self.values) - 1)
            return "break"
        if key == "BackSpace":
            self._typed = self._typed[:-1]
            self._typed_at = time.monotonic()
            if self._typed:
                self._jump_to(self._typed)
            return "break"
        # macOS Tk: Shift 0x1, Caps Lock 0x2, Control 0x4, Command 0x8,
        # Option 0x10. Command and Control keep their shortcuts. Everything
        # else, including Caps Lock, still jumps to a matching row.
        state = event.state or 0
        if state & (0x4 | 0x8):
            return
        char = event.char
        if (not char or not str(char).isprintable()) and len(key) == 1 and key.isprintable():
            char = key
        if not char or not str(char).isprintable():
            return
        self._type_char(char)
        return "break"

    def _move(self, delta):
        count = len(self.values)
        if count == 0:
            return
        current = self._pointed_index()
        if current is None:
            current = self._selected_index()
        base = -1 if current is None else current
        index = max(0, min(count - 1, base + delta))
        self._show_index(index)

    def _type_char(self, char):
        now = time.monotonic()
        if now - self._typed_at > self._TYPE_TIMEOUT:
            self._typed = ""
        self._typed_at = now
        trial = self._typed + char
        if self._jump_to(trial):
            self._typed = trial
            return
        if self._jump_to(char):
            self._typed = char
            return

    def _jump_to(self, prefix):
        folded = prefix.casefold()
        for index, value in enumerate(self.values):
            if value.casefold().startswith(folded):
                self._show_index(index)
                return True
        return False

    def _enable_keys(self):
        root.bind_class("ChoiceKeys", "<KeyPress>", self._on_key)

        def walk(widget):
            try:
                tags = widget.bindtags()
            except tk.TclError:
                return
            if "ChoiceKeys" not in tags:
                widget.bindtags(("ChoiceKeys",) + tuple(tags))
                self._keyed.append(widget)
            for child in widget.winfo_children():
                walk(child)

        walk(root)
        if self.panel is not None:
            walk(self.panel)

    def _disable_keys(self):
        for widget in self._keyed:
            try:
                widget.bindtags(tuple(tag for tag in widget.bindtags() if tag != "ChoiceKeys"))
            except tk.TclError:
                pass
        self._keyed = []
        try:
            root.unbind_class("ChoiceKeys", "<KeyPress>")
        except tk.TclError:
            pass

    def _focus_is_usable(self, widget, panel):
        if widget is None or widget is root:
            return False
        try:
            if not widget.winfo_exists():
                return False
        except tk.TclError:
            return False
        if panel is not None and (widget is panel or _widget_is_under(widget, panel)):
            return False
        return True

    def _restore_focus(self, panel):
        """Keep focus on a live field. Focusing the window itself makes the
        name and address boxes ignore clicks on macOS."""
        try:
            current = root.focus_get()
        except tk.TclError:
            current = None
        if self._focus_is_usable(current, panel):
            try:
                current.focus_force()
            except (tk.TclError, AttributeError):
                pass
            return
        target = self._focus_before
        if not self._focus_is_usable(target, panel):
            target = None
            try:
                target = name_entry
            except NameError:
                return
        try:
            target.focus_force()
        except (tk.TclError, AttributeError):
            pass

    def place(self):
        panel = self.panel
        if panel is None or self._placing:
            return
        self._placing = True
        try:
            try:
                hidden = not panel.winfo_viewable()
                x = self.button.winfo_rootx()
                y = self.button.winfo_rooty() + self.button.winfo_height()
                # Lay out while hidden. Idling a window that is already on
                # screen would paint the old position before the move.
                if hidden:
                    panel.update_idletasks()
                width = max(panel.winfo_reqwidth(), self.button.winfo_width(), 1)
                height = max(panel.winfo_reqheight(), 1)
            except tk.TclError:
                return
            screen_h = root.winfo_screenheight()
            if y + height > screen_h and self.button.winfo_rooty() > height:
                y = self.button.winfo_rooty() - height
            spot = (x, y, width, height)
            if self._spot == spot and not (hidden and self._reveal):
                return
            self._spot = spot
            try:
                panel.geometry(f"{width}x{height}+{x}+{y}")
                if hidden and self._reveal:
                    panel.deiconify()
                    # Mapping can put a help window back at the default spot.
                    # Apply the position again while it is still transparent.
                    panel.update_idletasks()
                    panel.geometry(f"{width}x{height}+{x}+{y}")
                    panel.update_idletasks()
                    try:
                        panel.attributes("-alpha", 1.0)
                    except tk.TclError:
                        pass
                panel.lift()
            except tk.TclError:
                pass
        finally:
            self._placing = False

    def close(self):
        global _open_choice
        panel = self.panel
        self.panel = None
        self.listbox = None
        self._spot = None
        self._reveal = False
        self._hover = None
        self._cursor = None
        self._from_keys = False
        self._typed = ""
        if _open_choice is self:
            _open_choice = None
        self._disable_keys()
        try:
            self.button.config(state="normal")
        except tk.TclError:
            pass
        self._restore_focus(panel)
        if panel is None:
            return
        try:
            panel.attributes("-topmost", False)
        except tk.TclError:
            pass
        try:
            panel.withdraw()
        except tk.TclError:
            pass

        def _destroy(panel=panel):
            try:
                if panel.winfo_exists():
                    panel.destroy()
            except tk.TclError:
                pass
            self._restore_focus(None)

        try:
            root.after(0, _destroy)
        except tk.TclError:
            _destroy()


# ───────────────────────────── GUI ─────────────────────────────
def build_gui():
    global root, state_var, state_dropdown, county_var, county_dropdown
    global name_entry, address_entry, search_button, loading_dot, loading_label
    global output, links_button, gold_dot, gold_label, gold_row, update_prompt, county_update_line
    global position_loading, auto_update_var, clean_view_var, map_link_type_var, map_link_dropdown

    root = tk.Tk()
    root.title("County Property Lookup")
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{int(sw * 0.5)}x{sh - 100}+0+0")

    status_frame = tk.Frame(root)
    status_frame.pack(fill="x", padx=10, pady=(8, 0), anchor="w")
    saved_auto_update = _read_auto_update_pref()
    if saved_auto_update:
        _auto_update_on.set()
    else:
        _auto_update_on.clear()
    auto_update_var = tk.BooleanVar(value=saved_auto_update)
    # Box and sentence are separate so the box can sit 2px lower on the text.
    # The row is only as wide as the box plus the words, so clicks past the
    # sentence do not toggle it.
    _pack_labeled_checkbutton(
        status_frame,
        "Allow database auto update?",
        auto_update_var,
        on_auto_update_toggle,
        anchor="w",
        pady=(1, 0),
    )
    gold_row = tk.Frame(status_frame)
    gold_row.pack(anchor="w", fill="x")
    # padx/bd stay 0 so the bullet lines up with the auto-update checkbox.
    # place() at a small negative x pulls the glyph 1–2px left of the checkbox.
    gold_dot = tk.Label(
        gold_row, text="●", font=("Arial", 18), fg="#6e5600",
        padx=0, pady=0, bd=0, highlightthickness=0,
    )
    gold_row.pack_propagate(False)
    gold_row.configure(height=gold_dot.winfo_reqheight())
    gold_dot.place(x=_GOLD_DOT_X, y=0)
    gold_label = tk.Label(
        gold_row, text="", font=("Arial", 10), fg="#ffffff",
        justify="left", anchor="w",
    )
    update_prompt = tk.Label(
        status_frame, text="", font=("Arial", 10), fg="#ffffff",
        justify="left", anchor="w",
    )
    # height=1 keeps this row the same size while the words are hidden, so the
    # controls under it do not jump when a county finishes.
    county_update_line = tk.Label(
        status_frame, text="", font=("Arial", 10), fg="#ffffff",
        justify="left", anchor="w", height=1,
    )
    county_update_line.pack(anchor="w", fill="x")

    dropdown_frame = tk.Frame(root)
    dropdown_frame.pack(pady=(10, 2))

    tk.Label(dropdown_frame, text="State:", font=("Arial", 12)).pack(
        side="left", padx=(0, 5), anchor="n",
    )
    state_var = tk.StringVar(value="Oregon")
    state_dropdown = ChoiceMenu(
        dropdown_frame, state_var, list(CONFIG.keys()) + [COUNTRYWIDE], 15,
    )
    state_dropdown.pack(side="left", padx=(0, 20), pady=(_DROPDOWN_Y_NUDGE, 0), anchor="n")

    tk.Label(dropdown_frame, text="County:", font=("Arial", 12)).pack(
        side="left", padx=(0, 5), anchor="n",
    )
    county_var = tk.StringVar(value=list(CONFIG["Oregon"].keys())[0])
    county_dropdown = ChoiceMenu(
        dropdown_frame, county_var, list(CONFIG["Oregon"].keys()) + ["Statewide"], 25,
    )
    county_dropdown.pack(side="left", pady=(_DROPDOWN_Y_NUDGE, 0), anchor="n")

    tk.Label(root, text="Enter Name:", font=("Arial", 12)).pack(pady=(10, 2))
    name_entry = tk.Entry(root, font=("Arial", 14), width=45)
    name_entry.pack(pady=2)
    name_entry.bind("<Button-1>", _focus_entry, add="+")
    name_entry.bind("<KeyRelease>", on_key_release)

    tk.Label(root, text="Enter Address (partial OK):", font=("Arial", 12)).pack(pady=(10, 2))
    address_entry = tk.Entry(root, font=("Arial", 14), width=45)
    address_entry.pack(pady=2)
    address_entry.bind("<Button-1>", _focus_entry, add="+")
    address_entry.bind("<KeyRelease>", on_key_release)

    root.bind("<Return>", lambda e: trigger_action())
    root.bind("<KP_Enter>", lambda e: trigger_action())

    control_frame = tk.Frame(root, height=55)
    control_frame.pack(fill="x", pady=10)
    control_frame.pack_propagate(False)

    search_button = tk.Button(control_frame, text="Search", font=("Arial", 12), command=search_name, width=28)
    search_button.place(relx=0.5, rely=0.5, anchor="center")
    search_button.config(state=tk.DISABLED)
    global searches_allowed
    searches_allowed = False

    loading_dot = tk.Label(control_frame, text="●", font=("Arial", 18), fg="#cc0000")
    loading_label = tk.Label(control_frame, text="", font=("Arial", 10), fg="#ffffff")

    def position_loading():
        root.update_idletasks()
        try:
            bx, by = search_button.winfo_x(), search_button.winfo_y()
            bw, bh = search_button.winfo_width(), search_button.winfo_height()
            dy = by + bh // 2
            dx = bx + bw + 12
            # The 18pt bullet's ink sits below the middle of its line box.
            # dy-2 keeps the bullet on the button; +1 lowers the 10pt label onto that ink.
            loading_dot.place(x=dx, y=dy - 2, anchor="w")
            loading_label.place(x=dx + loading_dot.winfo_width() + 4, y=dy + 1, anchor="w")
        except Exception:
            pass

    global _shown_state, _shown_county
    _shown_state = state_var.get()
    _shown_county = county_var.get()
    state_var.trace_add("write", update_county_dropdown)
    county_var.trace_add("write", on_county_change)

    root.after(0, lambda: (state_var.set("Oregon"), county_var.set(list(CONFIG["Oregon"].keys())[0])))
    root.after(50, position_loading)
    root.after(50, fit_status_wrap)
    root.bind("<Configure>", fit_status_wrap, add="+")
    root.bind("<FocusIn>", on_app_focus_change, add="+")
    root.bind("<FocusOut>", on_app_focus_change, add="+")

    search_button.bind("<Configure>", lambda e: position_loading())

    links_button_frame = tk.Frame(root)
    links_button_frame.pack(fill="x", padx=10, pady=(5, 0))
    links_button = tk.Button(
        links_button_frame,
        text="Display County Search Links",
        font=("Arial", 11),
        command=show_county_search_links,
    )

    results_opt_frame = tk.Frame(links_button_frame)
    # Pack the right-hand controls first so "Clean View" keeps its full width.
    results_opt_frame.pack(side="right", padx=(8, 0))
    links_button.pack(side="left")

    tk.Label(results_opt_frame, text="Map Link Type:", font=("Arial", 12)).pack(
        side="left", padx=(0, 5), anchor="n",
    )
    map_link_type_var = tk.StringVar(value=MAP_LINK_GOOGLE_MAPS)
    map_link_dropdown = ChoiceMenu(
        results_opt_frame, map_link_type_var, list(MAP_LINK_TYPES), 12,
    )
    map_link_dropdown.pack(
        side="left", padx=(0, 14), pady=(_DROPDOWN_Y_NUDGE, 0), anchor="n",
    )

    clean_view_var = tk.BooleanVar(value=False)
    _pack_labeled_checkbutton(
        results_opt_frame,
        "Clean View",
        clean_view_var,
        on_clean_view_toggle,
        side="left",
        pady=(_DROPDOWN_Y_NUDGE + 1, 0),
        anchor="n",
    )

    output = scrolledtext.ScrolledText(
        root, font=("Arial", 10), height=30, cursor="arrow",
        state=tk.NORMAL, takefocus=0, highlightthickness=0,
        insertwidth=0, undo=False, autoseparators=False,
    )
    output.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)
    output.bind("<Button-1>", _on_output_click)
    _bind_results_scroll()

    root.bind("<Button-1>", clear_entry_selections, add="+")
    root.after(100, begin_data_services)


if __name__ == "__main__":
    build_gui()
    root.mainloop()
