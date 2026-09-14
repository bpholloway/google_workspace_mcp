"""
Unit tests for cross-service scope generation.

Verifies that docs and sheets tools automatically include the Drive scopes
they need for operations like search_docs, list_docs_in_folder,
export_doc_to_pdf, and list_spreadsheets — without requiring --tools drive.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from auth.scopes import (
    BASE_SCOPES,
    CALENDAR_READONLY_SCOPE,
    CALENDAR_SCOPE,
    CONTACTS_READONLY_SCOPE,
    CONTACTS_SCOPE,
    DRIVE_FILE_SCOPE,
    DRIVE_READONLY_SCOPE,
    DRIVE_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    GMAIL_LABELS_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    GMAIL_SETTINGS_BASIC_SCOPE,
    SCOPE_HIERARCHY,
    SCRIPT_PROJECTS_SCOPE,
    SHEETS_READONLY_SCOPE,
    SHEETS_WRITE_SCOPE,
    _minimize_scopes,
    get_scopes_for_tools,
    has_required_scopes,
    set_read_only,
)
from auth.permissions import get_scopes_for_permission, set_permissions
import auth.permissions as permissions_module


class TestDocsScopes:
    """Tests for docs tool scope generation."""

    def test_docs_includes_drive_readonly(self):
        """search_docs, get_doc_content, list_docs_in_folder need drive.readonly."""
        scopes = get_scopes_for_tools(["docs"])
        assert DRIVE_READONLY_SCOPE in scopes

    def test_docs_includes_drive_file(self):
        """export_doc_to_pdf needs drive.file to create the PDF."""
        scopes = get_scopes_for_tools(["docs"])
        assert DRIVE_FILE_SCOPE in scopes

    def test_docs_does_not_include_full_drive(self):
        """docs should NOT request full drive access."""
        scopes = get_scopes_for_tools(["docs"])
        assert DRIVE_SCOPE not in scopes


class TestSheetsScopes:
    """Tests for sheets tool scope generation."""

    def test_sheets_includes_drive_readonly(self):
        """list_spreadsheets needs drive.readonly."""
        scopes = get_scopes_for_tools(["sheets"])
        assert DRIVE_READONLY_SCOPE in scopes

    def test_sheets_does_not_include_full_drive(self):
        """sheets should NOT request full drive access."""
        scopes = get_scopes_for_tools(["sheets"])
        assert DRIVE_SCOPE not in scopes


class TestCombinedScopes:
    """Tests for combined tool scope generation."""

    def test_docs_sheets_no_duplicate_drive_readonly(self):
        """Combined docs+sheets should deduplicate drive.readonly."""
        scopes = get_scopes_for_tools(["docs", "sheets"])
        assert scopes.count(DRIVE_READONLY_SCOPE) <= 1

    def test_docs_sheets_returns_unique_scopes(self):
        """All returned scopes should be unique."""
        scopes = get_scopes_for_tools(["docs", "sheets"])
        assert len(scopes) == len(set(scopes))


class TestReadOnlyScopes:
    """Tests for read-only mode scope generation."""

    def setup_method(self):
        set_read_only(False)

    def teardown_method(self):
        set_read_only(False)

    def test_docs_readonly_includes_drive_readonly(self):
        """Even in read-only mode, docs needs drive.readonly for search/list."""
        set_read_only(True)
        scopes = get_scopes_for_tools(["docs"])
        assert DRIVE_READONLY_SCOPE in scopes

    def test_docs_readonly_excludes_drive_file(self):
        """In read-only mode, docs should NOT request drive.file."""
        set_read_only(True)
        scopes = get_scopes_for_tools(["docs"])
        assert DRIVE_FILE_SCOPE not in scopes

    def test_sheets_readonly_includes_drive_readonly(self):
        """Even in read-only mode, sheets needs drive.readonly for list."""
        set_read_only(True)
        scopes = get_scopes_for_tools(["sheets"])
        assert DRIVE_READONLY_SCOPE in scopes


class TestHasRequiredScopes:
    """Tests for hierarchy-aware scope checking."""

    def test_exact_match(self):
        """Exact scope match should pass."""
        assert has_required_scopes([GMAIL_READONLY_SCOPE], [GMAIL_READONLY_SCOPE])

    def test_missing_scope_fails(self):
        """Missing scope with no covering broader scope should fail."""
        assert not has_required_scopes([GMAIL_READONLY_SCOPE], [GMAIL_SEND_SCOPE])

    def test_empty_available_fails(self):
        """Empty available scopes should fail when scopes are required."""
        assert not has_required_scopes([], [GMAIL_READONLY_SCOPE])

    def test_empty_required_passes(self):
        """No required scopes should always pass."""
        assert has_required_scopes([], [])
        assert has_required_scopes([GMAIL_READONLY_SCOPE], [])

    def test_none_available_fails(self):
        """None available scopes should fail when scopes are required."""
        assert not has_required_scopes(None, [GMAIL_READONLY_SCOPE])

    def test_none_available_empty_required_passes(self):
        """None available with no required scopes should pass."""
        assert has_required_scopes(None, [])

    # Gmail hierarchy: gmail.modify covers readonly, send, compose, labels
    def test_gmail_modify_covers_readonly(self):
        assert has_required_scopes([GMAIL_MODIFY_SCOPE], [GMAIL_READONLY_SCOPE])

    def test_gmail_modify_covers_send(self):
        assert has_required_scopes([GMAIL_MODIFY_SCOPE], [GMAIL_SEND_SCOPE])

    def test_gmail_modify_covers_compose(self):
        assert has_required_scopes([GMAIL_MODIFY_SCOPE], [GMAIL_COMPOSE_SCOPE])

    def test_gmail_modify_covers_labels(self):
        assert has_required_scopes([GMAIL_MODIFY_SCOPE], [GMAIL_LABELS_SCOPE])

    def test_gmail_modify_does_not_cover_settings(self):
        """gmail.modify does NOT cover gmail.settings.basic."""
        assert not has_required_scopes(
            [GMAIL_MODIFY_SCOPE], [GMAIL_SETTINGS_BASIC_SCOPE]
        )

    def test_gmail_modify_covers_multiple_children(self):
        """gmail.modify should satisfy multiple child scopes at once."""
        assert has_required_scopes(
            [GMAIL_MODIFY_SCOPE],
            [GMAIL_READONLY_SCOPE, GMAIL_SEND_SCOPE, GMAIL_LABELS_SCOPE],
        )

    # Drive hierarchy: drive covers drive.readonly and drive.file
    def test_drive_covers_readonly(self):
        assert has_required_scopes([DRIVE_SCOPE], [DRIVE_READONLY_SCOPE])

    def test_drive_covers_file(self):
        assert has_required_scopes([DRIVE_SCOPE], [DRIVE_FILE_SCOPE])

    def test_drive_readonly_does_not_cover_full(self):
        """Narrower scope should not satisfy broader scope."""
        assert not has_required_scopes([DRIVE_READONLY_SCOPE], [DRIVE_SCOPE])

    # Other hierarchies
    def test_calendar_covers_readonly(self):
        assert has_required_scopes([CALENDAR_SCOPE], [CALENDAR_READONLY_SCOPE])

    def test_sheets_write_covers_readonly(self):
        assert has_required_scopes([SHEETS_WRITE_SCOPE], [SHEETS_READONLY_SCOPE])

    def test_contacts_covers_readonly(self):
        assert has_required_scopes([CONTACTS_SCOPE], [CONTACTS_READONLY_SCOPE])

    # Mixed: some exact, some via hierarchy
    def test_mixed_exact_and_hierarchy(self):
        """Combination of exact matches and hierarchy-implied scopes."""
        available = [GMAIL_MODIFY_SCOPE, DRIVE_READONLY_SCOPE]
        required = [GMAIL_READONLY_SCOPE, DRIVE_READONLY_SCOPE]
        assert has_required_scopes(available, required)

    def test_mixed_partial_failure(self):
        """Should fail if hierarchy covers some but not all required scopes."""
        available = [GMAIL_MODIFY_SCOPE]
        required = [GMAIL_READONLY_SCOPE, DRIVE_READONLY_SCOPE]
        assert not has_required_scopes(available, required)


class TestGranularPermissionsScopes:
    """Tests for granular permissions scope generation path."""

    def setup_method(self):
        set_read_only(False)
        permissions_module._PERMISSIONS = None

    def teardown_method(self):
        set_read_only(False)
        permissions_module._PERMISSIONS = None

    def test_permissions_mode_returns_base_plus_permission_scopes(self):
        set_permissions({"gmail": "send", "drive": "readonly"})
        scopes = get_scopes_for_tools(["calendar"])  # ignored in permissions mode

        raw = set(BASE_SCOPES)
        raw.update(get_scopes_for_permission("gmail", "send"))
        raw.update(get_scopes_for_permission("drive", "readonly"))
        # "gmail":"send" is cumulative (auth/permissions.py) and includes
        # GMAIL_MODIFY_SCOPE, which covers readonly/labels/compose/send per
        # SCOPE_HIERARCHY -- the permissions-mode branch minimizes too (B6.2).
        assert set(scopes) == _minimize_scopes(raw)

    def test_permissions_mode_overrides_read_only_and_full_maps(self):
        set_read_only(True)
        without_permissions = get_scopes_for_tools(["drive"])
        assert DRIVE_READONLY_SCOPE in without_permissions

        set_permissions({"gmail": "readonly"})
        with_permissions = get_scopes_for_tools(["drive"])
        assert GMAIL_READONLY_SCOPE in with_permissions
        assert DRIVE_READONLY_SCOPE not in with_permissions


class TestScopeMinimization:
    """Tests for B6.2 (BL-113): get_scopes_for_tools drops any scope already
    implied by a broader scope also present, per SCOPE_HIERARCHY."""

    def setup_method(self):
        set_read_only(False)

    def teardown_method(self):
        set_read_only(False)

    def test_minimize_scopes_drops_narrower_when_parent_present(self):
        """Direct unit test of the minimization helper itself."""
        result = _minimize_scopes({DRIVE_SCOPE, DRIVE_READONLY_SCOPE, DRIVE_FILE_SCOPE})
        assert result == {DRIVE_SCOPE}

    def test_minimize_scopes_keeps_narrower_when_parent_absent(self):
        """No broader scope present -> nothing to collapse."""
        result = _minimize_scopes({DRIVE_READONLY_SCOPE})
        assert result == {DRIVE_READONLY_SCOPE}

    def test_minimize_scopes_is_a_noop_on_scopes_with_no_hierarchy_relation(self):
        result = _minimize_scopes({SCRIPT_PROJECTS_SCOPE})
        assert result == {SCRIPT_PROJECTS_SCOPE}

    def test_drive_group_collapses_readonly_and_file(self):
        """The 'drive' tool group itself requests DRIVE_SCOPE, DRIVE_READONLY_SCOPE,
        and DRIVE_FILE_SCOPE together (auth/scopes.py DRIVE_SCOPES) -- the two
        narrower ones must not survive minimization."""
        scopes = get_scopes_for_tools(["drive"])
        assert DRIVE_SCOPE in scopes
        assert DRIVE_READONLY_SCOPE not in scopes
        assert DRIVE_FILE_SCOPE not in scopes

    def test_gmail_group_collapses_to_modify_plus_settings(self):
        """The 'gmail' group requests readonly/send/compose/labels alongside
        modify (auth/scopes.py GMAIL_SCOPES); modify covers the first four."""
        scopes = get_scopes_for_tools(["gmail"])
        assert GMAIL_MODIFY_SCOPE in scopes
        for narrower in (
            GMAIL_READONLY_SCOPE,
            GMAIL_SEND_SCOPE,
            GMAIL_COMPOSE_SCOPE,
            GMAIL_LABELS_SCOPE,
        ):
            assert narrower not in scopes
        # Not covered by modify -- must survive.
        assert GMAIL_SETTINGS_BASIC_SCOPE in scopes

    def test_calendar_group_collapses_to_full_calendar(self):
        scopes = get_scopes_for_tools(["calendar"])
        assert CALENDAR_SCOPE in scopes
        assert CALENDAR_READONLY_SCOPE not in scopes

    def test_minimized_scopes_still_satisfy_has_required_scopes(self):
        """A tool declaring the narrower scope as its requirement must still pass
        has_required_scopes() against the minimized (broader-only) granted set --
        that's the whole point of SCOPE_HIERARCHY expansion staying unchanged."""
        granted = get_scopes_for_tools(["drive"])
        assert DRIVE_READONLY_SCOPE not in granted  # minimized away
        assert has_required_scopes(granted, [DRIVE_READONLY_SCOPE])  # still passes

    def test_every_hierarchy_parent_present_collapses_its_children(self):
        """General property check across the whole SCOPE_HIERARCHY table, not
        just the specific groups exercised above."""
        for parent, children in SCOPE_HIERARCHY.items():
            result = _minimize_scopes({parent, *children})
            assert result == {parent}, f"{parent} did not collapse {children}"
