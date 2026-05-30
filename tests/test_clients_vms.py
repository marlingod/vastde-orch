"""Tests for src/vastde_orch/clients/vms.py.

Mocks the vastpy.VASTClient since it uses dynamic __getattr__/__getitem__
to compose REST paths. We patch the class so VmsClient sees our MagicMock.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from vastde_orch.clients.vms import DiffResult, EnsureOutcome, VmsClient, _drift
from vastde_orch.config.models import VmsSpec


@pytest.fixture
def mock_vastpy(mocker: MockerFixture) -> MagicMock:
    """Replace vastpy.VASTClient inside vms module with a MagicMock instance."""
    fake = MagicMock(name="VASTClient_instance")
    mocker.patch("vastde_orch.clients.vms.VASTClient", return_value=fake)
    return fake


@pytest.fixture
def vms_spec() -> VmsSpec:
    return VmsSpec(address="vms.test", token="tok", tenant="default")


# ── _drift helper ───────────────────────────────────────────────────────────

class TestDrift:
    def test_only_returns_differing_keys(self) -> None:
        assert _drift({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == {"b": 2}

    def test_empty_when_identical(self) -> None:
        assert _drift({"a": 1}, {"a": 1, "extra": 99}) == {}

    def test_missing_in_observed_is_drift(self) -> None:
        assert _drift({"a": 1}, {}) == {"a": 1}

    def test_list_order_matters(self) -> None:
        assert _drift({"p": ["NFS", "SMB"]}, {"p": ["SMB", "NFS"]}) == {"p": ["NFS", "SMB"]}


# ── ensure() ────────────────────────────────────────────────────────────────

class TestEnsure:
    def test_creates_when_absent(self, mock_vastpy: MagicMock, vms_spec: VmsSpec) -> None:
        mock_vastpy.views.get.return_value = []
        mock_vastpy.views.post.return_value = {"id": 42}
        client = VmsClient(vms_spec)

        outcome = client.ensure(
            "views", key_field="path", key_value="/foo", spec={"path": "/foo", "x": 1}
        )

        assert outcome.result is DiffResult.CREATED
        assert outcome.id == 42
        mock_vastpy.views.get.assert_called_once_with(path="/foo")
        mock_vastpy.views.post.assert_called_once_with(path="/foo", x=1)

    def test_unchanged_when_no_drift(self, mock_vastpy: MagicMock, vms_spec: VmsSpec) -> None:
        mock_vastpy.views.get.return_value = [{"id": 7, "path": "/foo", "x": 1, "extra": "ignored"}]
        client = VmsClient(vms_spec)

        outcome = client.ensure(
            "views", key_field="path", key_value="/foo", spec={"path": "/foo", "x": 1}
        )

        assert outcome.result is DiffResult.UNCHANGED
        assert outcome.id == 7
        mock_vastpy.views.post.assert_not_called()
        # No PATCH either — no item lookup should have occurred
        assert not mock_vastpy.views.__getitem__.called

    def test_patches_drift(self, mock_vastpy: MagicMock, vms_spec: VmsSpec) -> None:
        mock_vastpy.views.get.return_value = [{"id": 7, "path": "/foo", "x": 1}]
        item = mock_vastpy.views.__getitem__.return_value
        client = VmsClient(vms_spec)

        outcome = client.ensure(
            "views", key_field="path", key_value="/foo", spec={"path": "/foo", "x": 2}
        )

        assert outcome.result is DiffResult.UPDATED
        assert outcome.drift == {"x": 2}
        mock_vastpy.views.__getitem__.assert_called_once_with(7)
        item.patch.assert_called_once_with(x=2)

    def test_patchable_fields_filters_drift(
        self, mock_vastpy: MagicMock, vms_spec: VmsSpec
    ) -> None:
        mock_vastpy.views.get.return_value = [{"id": 7, "path": "/foo", "x": 1, "y": 1}]
        item = mock_vastpy.views.__getitem__.return_value
        client = VmsClient(vms_spec)

        outcome = client.ensure(
            "views",
            key_field="path",
            key_value="/foo",
            spec={"path": "/foo", "x": 2, "y": 2},
            patchable_fields={"x"},
        )

        assert outcome.result is DiffResult.UPDATED
        assert outcome.drift == {"x": 2}
        item.patch.assert_called_once_with(x=2)

    def test_patchable_fields_excluding_all_drift_yields_unchanged(
        self, mock_vastpy: MagicMock, vms_spec: VmsSpec
    ) -> None:
        mock_vastpy.views.get.return_value = [{"id": 7, "path": "/foo", "y": 1}]
        client = VmsClient(vms_spec)

        outcome = client.ensure(
            "views",
            key_field="path",
            key_value="/foo",
            spec={"path": "/foo", "y": 2},
            patchable_fields=set(),  # no fields are patchable
        )

        assert outcome.result is DiffResult.UNCHANGED
        assert not mock_vastpy.views.__getitem__.return_value.patch.called

    def test_dry_run_would_create(self, mock_vastpy: MagicMock, vms_spec: VmsSpec) -> None:
        mock_vastpy.views.get.return_value = []
        client = VmsClient(vms_spec, dry_run=True)

        outcome = client.ensure(
            "views", key_field="path", key_value="/foo", spec={"path": "/foo"}
        )

        assert outcome.result is DiffResult.WOULD_CREATE
        mock_vastpy.views.post.assert_not_called()

    def test_dry_run_would_update(self, mock_vastpy: MagicMock, vms_spec: VmsSpec) -> None:
        mock_vastpy.views.get.return_value = [{"id": 5, "path": "/foo", "x": 1}]
        client = VmsClient(vms_spec, dry_run=True)

        outcome = client.ensure(
            "views", key_field="path", key_value="/foo", spec={"path": "/foo", "x": 9}
        )

        assert outcome.result is DiffResult.WOULD_UPDATE
        assert outcome.drift == {"x": 9}
        assert not mock_vastpy.views.__getitem__.return_value.patch.called


# ── delete() ────────────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_absent_returns_unchanged(
        self, mock_vastpy: MagicMock, vms_spec: VmsSpec
    ) -> None:
        mock_vastpy.views.get.return_value = []
        client = VmsClient(vms_spec)
        out = client.delete("views", key_field="path", key_value="/foo")
        assert out.result is DiffResult.UNCHANGED

    def test_delete_present(self, mock_vastpy: MagicMock, vms_spec: VmsSpec) -> None:
        mock_vastpy.views.get.return_value = [{"id": 12, "path": "/foo"}]
        item = mock_vastpy.views.__getitem__.return_value
        client = VmsClient(vms_spec)
        out = client.delete("views", key_field="path", key_value="/foo")
        assert out.result is DiffResult.DELETED
        assert out.id == 12
        item.delete.assert_called_once_with()

    def test_delete_dry_run(self, mock_vastpy: MagicMock, vms_spec: VmsSpec) -> None:
        mock_vastpy.views.get.return_value = [{"id": 12, "path": "/foo"}]
        item = mock_vastpy.views.__getitem__.return_value
        client = VmsClient(vms_spec, dry_run=True)
        out = client.delete("views", key_field="path", key_value="/foo")
        assert out.result is DiffResult.WOULD_DELETE
        assert not item.delete.called


# ── typed conveniences spot-check ───────────────────────────────────────────

class TestTypedHelpers:
    def test_ensure_tenant(self, mock_vastpy: MagicMock, vms_spec: VmsSpec) -> None:
        mock_vastpy.tenants.get.return_value = []
        mock_vastpy.tenants.post.return_value = {"id": 100}
        client = VmsClient(vms_spec)

        out = client.ensure_tenant("my-tenant", domain="d1")
        assert out.result is DiffResult.CREATED
        mock_vastpy.tenants.post.assert_called_once_with(name="my-tenant", domain="d1")

    def test_ensure_view_only_patches_whitelisted_fields(
        self, mock_vastpy: MagicMock, vms_spec: VmsSpec
    ) -> None:
        # Existing view has different bucket_owner (not patchable) and different protocols (is).
        mock_vastpy.views.get.return_value = [{
            "id": 1, "path": "/a", "policy_id": 5,
            "protocols": ["NFS"], "bucket_owner": "alice", "create_dir": True,
        }]
        item = mock_vastpy.views.__getitem__.return_value
        client = VmsClient(vms_spec)

        out = client.ensure_view(
            "/a", policy_id=5, protocols=["NFS", "SMB"],
            bucket_name="b", bucket_owner="bob",
        )

        assert out.result is DiffResult.UPDATED
        item.patch.assert_called_once_with(protocols=["NFS", "SMB"])

    def test_ensure_vippool_strips_cidr_to_mask(
        self, mock_vastpy: MagicMock, vms_spec: VmsSpec
    ) -> None:
        """VAST stores subnet_cidr as just the mask suffix (e.g. '24'),
        not the full CIDR. ensure_vippool must accept either form."""
        mock_vastpy.vippools.get.return_value = []
        mock_vastpy.vippools.post.return_value = {"id": 1}
        client = VmsClient(vms_spec)

        client.ensure_vippool(
            "pool", tenant_id=15,
            cidr="172.200.203.0/16",
            ip_range_start="172.200.203.45",
            ip_range_end="172.200.203.48",
        )
        body = mock_vastpy.vippools.post.call_args.kwargs
        assert body["subnet_cidr"] == "16"

    def test_ensure_vippool_accepts_bare_mask_too(
        self, mock_vastpy: MagicMock, vms_spec: VmsSpec
    ) -> None:
        mock_vastpy.vippools.get.return_value = []
        mock_vastpy.vippools.post.return_value = {"id": 1}
        client = VmsClient(vms_spec)
        client.ensure_vippool(
            "pool", tenant_id=15, cidr="24",
            ip_range_start="1.1.1.1", ip_range_end="1.1.1.2",
        )
        assert mock_vastpy.vippools.post.call_args.kwargs["subnet_cidr"] == "24"

    # ── manager / role / realm helpers ──

    def test_ensure_role_minimal_body(
        self, mock_vastpy: MagicMock, vms_spec: VmsSpec
    ) -> None:
        mock_vastpy.roles.get.return_value = []
        mock_vastpy.roles.post.return_value = {"id": 99}
        client = VmsClient(vms_spec)
        out = client.ensure_role("demo-admin-role", tenant_id=15)
        body = mock_vastpy.roles.post.call_args.kwargs
        assert body == {"name": "demo-admin-role", "tenant_id": 15}
        assert out.result is DiffResult.CREATED

    def test_ensure_manager_includes_required_fields(
        self, mock_vastpy: MagicMock, vms_spec: VmsSpec
    ) -> None:
        mock_vastpy.managers.get.return_value = []
        mock_vastpy.managers.post.return_value = {"id": 50}
        client = VmsClient(vms_spec)
        client.ensure_manager(
            "demo-admin", tenant_id=15, user_type="TENANT_ADMIN", role_ids=[99],
            first_name="Demo", last_name="Admin",
        )
        body = mock_vastpy.managers.post.call_args.kwargs
        assert body["username"] == "demo-admin"
        assert body["tenant_id"] == 15
        assert body["user_type"] == "TENANT_ADMIN"
        assert body["roles"] == [99]
        assert body["is_active"] is True

    def test_set_manager_password_dry_run_does_not_call_api(
        self, mock_vastpy: MagicMock, vms_spec: VmsSpec
    ) -> None:
        client = VmsClient(vms_spec, dry_run=True)
        out = client.set_manager_password("demo-admin", "s3cret")
        assert out.result is DiffResult.WOULD_UPDATE
        assert "password" in out.drift  # masked
        assert not mock_vastpy.managers.password.patch.called

    def test_set_manager_password_real(
        self, mock_vastpy: MagicMock, vms_spec: VmsSpec
    ) -> None:
        # PATCH /managers/{id}/ with just {password} — NOT /managers/password/.
        mock_vastpy.managers.get.return_value = [{"id": 50, "username": "demo-admin"}]
        client = VmsClient(vms_spec)
        out = client.set_manager_password("demo-admin", "s3cret")
        mock_vastpy.managers.__getitem__.assert_called_with(50)
        mock_vastpy.managers.__getitem__.return_value.patch.assert_called_once_with(
            password="s3cret",
        )
        assert out.result is DiffResult.UPDATED

    def test_assign_role_to_realm(self, mock_vastpy: MagicMock, vms_spec: VmsSpec) -> None:
        client = VmsClient(vms_spec)
        out = client.assign_role_to_realm(realm_id=1, role_id=99)
        # mock_vastpy.realms[1] is the resource handle
        mock_vastpy.realms.__getitem__.assert_called_with(1)
        mock_vastpy.realms.__getitem__.return_value.assign.patch.assert_called_once_with(role_id=99)
        assert out.result is DiffResult.UPDATED

    def test_get_or_raise_raises_when_missing(
        self, mock_vastpy: MagicMock, vms_spec: VmsSpec
    ) -> None:
        mock_vastpy.tenants.get.return_value = []
        client = VmsClient(vms_spec)
        with pytest.raises(LookupError, match="tenants"):
            client.get_or_raise("tenants", key_field="name", key_value="x")

    def test_generate_s3_keys_dry_run(self, mock_vastpy: MagicMock, vms_spec: VmsSpec) -> None:
        client = VmsClient(vms_spec, dry_run=True)
        keys = client.generate_s3_keys(user_id=1)
        assert "<dry-run>" in keys["secret_key"]
        # No real call
        assert not mock_vastpy.users.__getitem__.called
