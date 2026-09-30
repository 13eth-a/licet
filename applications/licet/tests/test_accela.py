from licet.browser import accela


APPLY_URLS = [
    (
        f"{accela.PORTAL_ROOT}/Cap/CapApplyDisclaimer.aspx?module=Building",
        "disclaimer",
    ),
    (f"{accela.PORTAL_ROOT}/Cap/CapType.aspx?stepNumber=1", "type"),
    (
        f"{accela.PORTAL_ROOT}/Cap/CapEdit.aspx?module=Building&stepNumber=1&pageNumber=1",
        "form",
    ),
    (
        f"{accela.PORTAL_ROOT}/Cap/CapEdit.aspx?module=Building&stepNumber=2&pageNumber=2",
        "contact",
    ),
    (
        f"{accela.PORTAL_ROOT}/Cap/CapEdit.aspx?module=Building&stepNumber=3&pageNumber=3",
        "detail",
    ),
    (f"{accela.PORTAL_ROOT}/Cap/CapConfirm.aspx?stepNumber=5", "review"),
]


def test_locate_apply_wizard_steps():
    for url, expected_step in APPLY_URLS:
        position = accela.locate(url)
        assert position is not None, url
        assert position.flow == accela.APPLY_FLOW.name
        assert position.step == expected_step, url


# the scheduling wizard shares one url for every step, so the popup's own wording is the only way to know
# where we are (captured live 2026-09-20)
SCHEDULING_URL = (
    "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx?Module=Building"
    "&TabName=Building&capID1=REC26&capID2=00000&capID3=000QC"
    "&agencyCode=NULLISLAND&IsToShowInspection=yes"
)


def test_locate_refines_the_scheduling_step_from_page_text():
    types = accela.locate(
        SCHEDULING_URL,
        "Schedule/Request an Inspection | Available Inspection Types (13) | Continue | Cancel",
    )
    assert (types.flow, types.step) == ("schedule_inspection", "select_type")

    calendar = accela.locate(
        SCHEDULING_URL,
        "Inspection type: Floor Deck | To continue, select an appointment date and time "
        "range by clicking a link on the calendar below: | Sep 2026 | Su | Mo",
    )
    assert (calendar.flow, calendar.step) == ("schedule_inspection", "select_date")


def test_locate_stops_at_select_record_without_text():
    """url-only callers (the guard) must keep working unchanged"""
    position = accela.locate(SCHEDULING_URL)

    assert (position.flow, position.step) == ("schedule_inspection", "select_record")


def test_schedule_step_is_none_off_the_wizard():
    assert accela.schedule_step("Logout | Record Info | Payments") is None


def test_locate_other_pages():
    assert accela.locate(accela.MY_RECORDS_URL).flow == "my_records"
    assert accela.locate(accela.INSPECTION_ENTRY_URL).flow == accela.SCHEDULE_FLOW.name
    assert accela.locate(accela.INSPECTION_ENTRY_URL).step == "select_record"
    assert accela.locate(
        accela.detail_url("REC26", "00000", "000QG")
    ).flow == "record_detail"
    assert accela.locate("https://example.test/about") is None
    assert accela.locate("") is None


def test_apply_flow_commit_point_is_a_submission():
    """a continue click on the review step issues the record on ni"""
    assert accela.APPLY_FLOW.commit_step == "review"
    assert accela.APPLY_FLOW.commit_action == "submit_application"


def test_detail_url_matches_the_verified_shape():
    url = accela.detail_url("REC26", "00000", "000QG")
    assert url == (
        "https://aca-test.accela.com/NULLISLAND/Cap/CapDetail.aspx"
        "?Module=Building&TabName=Building"
        "&capID1=REC26&capID2=00000&capID3=000QG"
        "&agencyCode=NULLISLAND&IsToShowInspection="
    )
    # site-absolute, not agency-relative: prefixing the agency path 404s
    assert "/nullisland/NULLISLAND/" not in url


ROW_USE_ERROR_PANEL = """
<span id="ErrorList1"><div class="ACA_Message_Error">
<a href="javascript:void(0)" onclick="myValidationErrorPanel.skipTo(
   'ctl00_PlaceHolderMain_AppSpec5109C0E9Edit_NULLISLAND_txt_3_0v_a_l_i_d1',true)"
   id="ctl00_PlaceHolderMain_AppSpec5109C0E9Edit_NULLISLAND_txt_3_0v_a_l_i_d0"></a>
<a href="javascript:void(0)" onclick="myValidationErrorPanel.skipTo(
   'ctl00_PlaceHolderMain_AppSpec5109C0E9Edit_NULLISLAND_txt_3_0',false)"
   id="ctl00_PlaceHolderMain_AppSpec5109C0E9Edit_NULLISLAND_txt_3_0v_a_l_i_d1"
   class="ACA_Message_Error_Link">1.Schedule Start Date: Required Enter as MM/dd/yyyy</a>
<a href="javascript:void(0)" onclick="myValidationErrorPanel.skipTo(
   'ctl00_PlaceHolderMain_AppSpec5109C0E9Edit_NULLISLAND_txt_3_1',false)"
   class="ACA_Message_Error_Link">2.Estimated Completion Date: Required</a>
</div></span>
"""

ROW_USE_FIELDS = """
<span class="ACA_Label font12px"><label
  id="ctl00_PlaceHolderMain_AppSpec5109C0E9Edit_NULLISLAND_txt_3_0_label_1"
  for="ctl00_PlaceHolderMain_AppSpec5109C0E9Edit_NULLISLAND_txt_3_0"
  class="ACA_Error_Label">Schedule Start Date: </label></span>
<input name="ctl00$PlaceHolderMain$AppSpec5109C0E9Edit$NULLISLAND_txt_3_0"
  type="text" id="ctl00_PlaceHolderMain_AppSpec5109C0E9Edit_NULLISLAND_txt_3_0"
  title="Required" class="ACA_NLonger maskedfields HighlightCssClass MaskedEditError"
  aria-required="true" isasi="true" aria-label="MM/DD/YYYY"
  placeholder="MM/DD/YYYY" fieldname="Schedule Start Date" groupname="BLD_ROW">
<input name="ctl00$PlaceHolderMain$WorkLocationEdit$txtZip" type="text"
  id="ctl00_PlaceHolderMain_WorkLocationEdit_txtZip" title="Required"
  class="maskedfields" fieldname="Zip" value="">
<select name="ctl00$PlaceHolderMain$ddlSearchType"
  id="ctl00_PlaceHolderMain_ddlSearchType" title="Required">
  <option value="">-- Select --</option>
  <option value="addr" selected>Search by Address</option>
  <option value="permit">Search by Record Number</option>
</select>
"""


def test_validation_errors_pair_control_with_message_and_drop_label_targets():
    errors = accela.parse_validation_errors(ROW_USE_ERROR_PANEL)
    assert [error.control_id for error in errors] == [
        "ctl00_PlaceHolderMain_AppSpec5109C0E9Edit_NULLISLAND_txt_3_0",
        "ctl00_PlaceHolderMain_AppSpec5109C0E9Edit_NULLISLAND_txt_3_1",
    ]
    assert errors[0].message == "Schedule Start Date: Required Enter as MM/dd/yyyy"
    assert errors[1].message == "Estimated Completion Date: Required"


def test_validation_targets_helpers_agree():
    assert accela.validation_targets(ROW_USE_ERROR_PANEL) == [
        error.control_id for error in accela.parse_validation_errors(ROW_USE_ERROR_PANEL)
    ]


def test_parse_fields_exposes_label_required_masked_and_options():
    fields = {field.label: field for field in accela.parse_fields(ROW_USE_FIELDS)}

    start = fields["Schedule Start Date"]
    assert start.required is True
    assert start.masked is True
    assert start.kind == "text"

    assert fields["Zip"].masked is True

    # selects are keyed by their own label/fieldname, not by their options
    select = next(field for field in accela.parse_fields(ROW_USE_FIELDS) if field.kind == "select")
    assert select.required is True
    assert select.postback is True
    assert "Search by Address" in select.options
    assert select.value == "Search by Address"


def test_detect_loading_markers():
    assert accela.detect_loading("Inspections | Loading... | Post") == ["loading..."]
    assert accela.is_loading("please wait")
    assert not accela.is_loading("Upcoming | There are no completed inspections")


def test_detect_notices_and_postback_detection():
    assert accela.detect_notices("Please login to continue") == ["please login to continue"]
    assert accela.detect_notices("Nothing to see") == []
    assert accela.has_postback_history('<input name="__VIEWSTATE" value="abc">')
    assert accela.has_postback_history('onclick="__doPostBack(...)"')
    assert not accela.has_postback_history("<html><body>plain</body></html>")


TYPE_GRID_HTML = """
<table id="ctl00_phPopup_gvInspectionType">
<tr>
  <td><label for="ctl00_phPopup_gvInspectionType_ctl02_rdInspectionType">Brycer Inspection History (required)</label></td>
  <td><input type="radio" id="ctl00_phPopup_gvInspectionType_ctl02_rdInspectionType" name="ctl00$phPopup$gvInspectionType$ctl02$rdInspectionType" value="84043150" /></td>
</tr>
<tr>
  <td><label for="ctl00_phPopup_gvInspectionType_ctl03_rdInspectionType">Set Backs (optional)</label></td>
  <td><input type="radio" id="ctl00_phPopup_gvInspectionType_ctl03_rdInspectionType" name="ctl00$phPopup$gvInspectionType$ctl03$rdInspectionType" value="97" /></td>
</tr>
<tr>
  <td><label for="ctl00_phPopup_gvInspectionType_ctl04_rdInspectionType">Floor Deck (required)</label></td>
  <td><input type="radio" id="ctl00_phPopup_gvInspectionType_ctl04_rdInspectionType" name="ctl00$phPopup$gvInspectionType$ctl04$rdInspectionType" value="103" /></td>
</tr>
</table>
<input type="checkbox" id="ctl00_phPopup_chkShowOptional" name="ctl00$phPopup$chkShowOptional" />
<input type="radio" id="ctl00_PlaceHolderMain_addForDetailPage_rdoNewCollection" name="ctl00$PlaceHolderMain$addForDetailPage$collection" value="rdoNewCollection" />
"""

TYPE_GRID_TEXT = (
    "Schedule/Request an Inspection | Available Inspection Types (18) | "
    "Show optional inspections | Brycer Inspection History (required) | "
    "< Prev 1 2 Next > | Continue | Cancel"
)


def test_parse_inspection_types_reads_names_and_required_marker():
    options = accela.parse_inspection_types(accela.parse_fields(TYPE_GRID_HTML))

    assert [option.name for option in options] == [
        "Brycer Inspection History",
        "Set Backs",
        "Floor Deck",
    ]
    # the marker is the only signal for whether the rest can be skipped
    assert [option.required for option in options] == [True, False, True]
    assert options[0].value == "84043150"  # aca's own type id, not the row id
    assert options[0].control_id == "ctl00_phPopup_gvInspectionType_ctl02_rdInspectionType"


def test_parse_inspection_types_ignores_the_collections_radio():
    options = accela.parse_inspection_types(accela.parse_fields(TYPE_GRID_HTML))

    assert all("addForDetailPage" not in option.control_id for option in options)


def test_parse_inspection_types_accepts_serialised_fields():
    """read_page hands back dicts, and the names must survive that trip"""
    serialised = [f.as_dict() for f in accela.parse_fields(TYPE_GRID_HTML)]

    assert accela.parse_inspection_types(serialised) == accela.parse_inspection_types(
        accela.parse_fields(TYPE_GRID_HTML)
    )


def test_parse_inspection_types_falls_back_to_the_label_marker():
    """if aca ever renders the grid under a different prefix, the marker saves us"""
    html = (
        '<label for="ctl00_Other_ctl02_rdType">Rough (optional)</label>'
        '<input type="radio" id="ctl00_Other_ctl02_rdType" value="73" />'
    )

    options = accela.parse_inspection_types(accela.parse_fields(html))

    assert [(o.name, o.required) for o in options] == [("Rough", False)]


def test_parse_inspection_types_is_empty_without_a_wizard():
    assert accela.parse_inspection_types(accela.parse_fields(ROW_USE_FIELDS)) == []


def test_inspection_type_total_spans_the_paginated_grid():
    """page 1 shows 10 rows of commercial alteration's 18 — never trust the rows"""
    assert accela.inspection_type_total(TYPE_GRID_TEXT) == 18
    assert accela.inspection_type_total("Available Inspection Types (0)") == 0
    assert accela.inspection_type_total("no heading here") is None


# verbatim shapes from the null island popup: day cells are <td>, not anchors, and an unbookable day says
# so in both class and title
CALENDAR_HTML = """
<table role="presentation" class="InspectionWizardPageWidth"><tbody><tr valign="top">
<td class="ACA_Calendar_Cell">
 <table id="ctl00_phPopup_calendar_calendar1" title="Calendar" class="ACA_Calendar_Container">
  <caption><span class="font12px">Sep 2026</span></caption>
  <tbody>
  <tr><td title="Cannot schedule inspection on this date" class="CalendarDayInactive ACA_LinkButton" align="center">1</td>
      <td title="Cannot schedule inspection on this date" class="CalendarDayInactive ACA_LinkButton" align="center">2</td></tr>
  <tr><td class="CalendarDay ACA_LinkButton CalendarDayAvailable" align="center">21</td>
      <td class="CalendarDay ACA_LinkButton CalendarDayAvailable" align="center">22</td></tr>
  </tbody>
 </table>
 <table id="ctl00_phPopup_calendar_calendar2" title="Calendar" class="ACA_Calendar_Container">
  <caption><span class="font12px">Oct 2026</span></caption>
  <tbody>
  <tr><td title="Cannot schedule inspection on this date" class="CalendarDayInactive ACA_LinkButton" align="center">3</td></tr>
  </tbody>
 </table>
</td></tr></tbody></table>
<span id="ctl00_phPopup_calendar_lblAvaliableTimes" class="ACA_Title_Color"></span>
<a id="ctl00_phPopup_lnkContinue" title="Continue" href="javascript:void(0);" href_disabled="javascript:__doPostBack('ctl00$phPopup$lnkContinue','')" disabled="disabled" class="ButtonDisabled"><span>Continue</span></a>
"""


def test_parse_calendar_splits_active_and_inactive_days_per_month():
    months = accela.parse_calendar(CALENDAR_HTML)

    assert [month.month for month in months] == ["Sep 2026", "Oct 2026"]
    assert months[0].active_days == (21, 22)
    assert months[0].inactive_days == (1, 2)
    assert months[0].any_available is True
    assert months[1].any_available is False


def test_parse_calendar_reports_a_fully_unavailable_month():
    """the sandbox case: nothing bookable, so the goal is unachievable, not slow"""
    html = CALENDAR_HTML.replace("CalendarDay ACA_LinkButton CalendarDayAvailable", "CalendarDayInactive ACA_LinkButton")

    months = accela.parse_calendar(html)

    assert all(not month.any_available for month in months)
    assert months[0].active_days == ()


def test_selectable_times_is_empty_until_a_day_is_picked():
    assert accela.selectable_times_text(CALENDAR_HTML) == ""
    with_times = CALENDAR_HTML.replace(
        'lblAvaliableTimes" class="ACA_Title_Color"></span>',
        'lblAvaliableTimes" class="ACA_Title_Color">8:00 am - 12:00 pm</span>',
    )
    assert accela.selectable_times_text(with_times) == "8:00 am - 12:00 pm"


def test_popup_continue_disabled_detects_the_stashed_postback():
    """aca hides the real postback in `href_disabled`; force-clicking would fire it"""
    assert accela.popup_continue_disabled(CALENDAR_HTML) is True
    enabled = CALENDAR_HTML.replace(' disabled="disabled"', "")
    assert accela.popup_continue_disabled(enabled) is False


def test_parse_calendar_is_empty_without_a_calendar():
    assert accela.parse_calendar(ROW_USE_FIELDS) == []


def test_field_and_select_parsers_accept_single_quoted_aca_fragments():
    html = """
    <label for='zip'>Zip</label>
    <input type='text' id='zip' fieldname='Zip' class='maskedfields' title='Required' value='02108'>
    <select id='searchType' title='Required'>
      <option value='addr' selected='selected'>Address</option>
    </select>
    """
    fields = accela.parse_fields(html)
    assert fields[0].label == "Zip"
    assert fields[0].value == "02108"
    select = next(field for field in fields if field.kind == "select")
    assert select.value == "Address"
    assert select.options == ("Address",)


def test_parse_ref_accepts_case_variants_and_url_encoded_values():
    ref = accela.parse_ref_from_url(
        "https://aca-test.example/Cap/CapDetail.aspx?capid1=REC%2026&CAPID2=00000"
        "&CapId3=000Q%2B&MODULE=Building&AGENCYCODE=NULLISLAND"
    )
    assert ref == {
        "capID1": "REC 26",
        "capID2": "00000",
        "capID3": "000Q+",
        "module": "Building",
        "agency_code": "NULLISLAND",
    }


SEARCH_FORM_HTML = """
<select id="ctl00_PlaceHolderMain_ddlSearchType" fieldname="Search Type">
  <option value="GS" selected="selected">Permit Number</option>
  <option value="APO">Search by Address</option>
  <option value="PARCEL">Search by Parcel</option>
</select>
<input type="text" id="ctl00_PlaceHolderMain_generalSearchForm_txtGSPermitNumber" fieldname="Record Number">
<input type="text" id="ctl00_PlaceHolderMain_generalSearchForm_txtGSStartDate" value="09/18/2024">
"""

APO_FORM_HTML = """
<select id="ctl00_PlaceHolderMain_ddlSearchType">
  <option value="GS">Permit Number</option>
  <option value="APO" selected="selected">Search by Address</option>
</select>
<input type="text" id="ctl00_PlaceHolderMain_apoSearchForm_txtAPO_Search_by_Address_StreetNumber_ChildControl0">
<input type="text" id="ctl00_PlaceHolderMain_apoSearchForm_txtAPO_Search_by_Address_StreetName">
<input type="text" id="ctl00_PlaceHolderMain_generalSearchForm_txtGSStartDate" value="09/18/2024">
"""


def test_search_mode_option_matches_agency_wording_case_insensitively():
    labels = ["Permit Number", "Search by Address", "Search by Parcel"]
    assert accela.search_mode_option(labels, "address") == "Search by Address"
    assert accela.search_mode_option(labels, "parcel") == "Search by Parcel"
    assert accela.search_mode_option(labels, "record_number") == "Permit Number"
    # no such mode on this agency: say so, do not guess
    assert accela.search_mode_option(labels, "applicant") is None


def test_resolve_search_field_finds_the_rendered_family():
    fields = accela.parse_fields(APO_FORM_HTML)
    assert accela.resolve_search_field(fields, "street_name").endswith(
        "txtAPO_Search_by_Address_StreetName"
    )
    gs_fields = accela.parse_fields(SEARCH_FORM_HTML)
    assert accela.resolve_search_field(gs_fields, "record_number").endswith("txtGSPermitNumber")
    assert accela.resolve_search_field(APO_FORM_HTML and fields, "parcel_number") is None


def test_resolve_search_field_accepts_read_page_dicts():
    read_page_fields = [f.as_dict() for f in accela.parse_fields(APO_FORM_HTML)]
    assert accela.resolve_search_field(read_page_fields, "street_name") is not None


def test_zero_results_and_table_detection():
    assert accela.looks_like_zero_results("No records found for your search.")
    assert accela.looks_like_zero_results("0 results")
    assert not accela.looks_like_zero_results("12 records match your search")
    assert accela.has_results_table("<th>Record Number</th>")
    assert not accela.has_results_table("<div>An error occurred</div>")


def test_search_url_targets_the_building_module_by_default():
    assert accela.search_url() == (
        f"{accela.PORTAL_ROOT}/Cap/CapHome.aspx?TabName=Home&module=Building"
    )
