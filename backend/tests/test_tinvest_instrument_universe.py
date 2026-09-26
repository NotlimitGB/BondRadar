from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.schemas.tinvest_instrument_universe import (
    TInvestAvailabilityClass,
    TInvestBondsBaseResponse,
    TInvestBondUniverseInstrument,
    TInvestDfasResponse,
    TInvestDfaUniverseInstrument,
    TInvestUniverseFailureCode,
    TInvestUniverseSourceError,
)
from app.services.tinvest_instrument_universe_client import (
    BONDS_BASE_REQUEST,
    BONDS_ROUTE,
    DFAS_REQUEST,
    DFAS_ROUTE,
    TINVEST_REST_BASE,
    TInvestInstrumentUniverseClient,
)
from app.services.tinvest_instrument_universe_service import (
    TInvestInstrumentUniverseService,
)


SYNTHETIC_TOKEN = "synthetic-task295-token-not-a-secret"


def bond_row(uid: str = "bond-uid-1", **updates: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "uid": uid,
        "positionUid": "position-1",
        "assetUid": "asset-1",
        "figi": "FIGI-1",
        "isin": "RU000A000001",
        "ticker": "BOND1",
        "classCode": "TQCB",
        "name": "Synthetic bond",
        "lot": 1,
        "currency": "rub",
        "buyAvailableFlag": True,
        "sellAvailableFlag": False,
        "apiTradeAvailableFlag": True,
        "forQualInvestorFlag": True,
        "requiredTests": ["test-a", "test-b"],
        "floatingCouponFlag": False,
        "perpetualFlag": False,
        "amortizationFlag": True,
        "subordinatedFlag": False,
        "maturityDate": "2030-01-01T00:00:00Z",
        "callDate": "2029-01-01T00:00:00Z",
        "unmodeledExactSourceField": {"preserve": [1, "two"]},
    }
    row.update(updates)
    return row


def dfa_row(uid: str = "dfa-uid-1", **updates: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "uid": uid,
        "positionUid": "dfa-position-1",
        "ticker": "DFA1",
        "name": "Synthetic DFA",
        "lot": 2,
        "currency": "rub",
        "maturityDate": "2031-05-07T00:00:00Z",
        "apiTradeAvailableFlag": True,
        "buyAvailableFlag": False,
        "sellAvailableFlag": True,
        "limitOrderAvailableFlag": True,
        "marketOrderAvailableFlag": False,
        "bestpriceOrderAvailableFlag": True,
        "forIisFlag": False,
        "forQualInvestorFlag": True,
        "type": "debt_dfa",
        "basicAssets": [{"uid": "underlying-1"}],
        "nominal": {"units": "100", "nano": 25},
        "yieldToMaturity": {"units": "9", "nano": 0},
        "couponValue": {"units": "1", "nano": 500000000},
        "couponPaymentFrequency": 4,
        "aciValue": {"units": "0", "nano": 5},
        "forecastYield": {"minValue": {"units": "8", "nano": 0}},
    }
    row.update(updates)
    return row


def reduce(bonds: list[dict[str, Any]], dfas: list[dict[str, Any]] | None = None):
    return TInvestInstrumentUniverseService.reduce_current_universe(
        TInvestBondsBaseResponse(response={"instruments": bonds}),
        TInvestDfasResponse(response={"instruments": dfas or []}),
    )


def test_client_uses_only_exact_base_bonds_and_empty_dfas_requests() -> None:
    requests: list[tuple[str, dict[str, Any]]] = []
    authorization_matches: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        authorization_matches.append(
            request.headers.get("authorization", "")
            == f"Bearer {SYNTHETIC_TOKEN}"
        )
        return httpx.Response(200, json={"instruments": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        source = TInvestInstrumentUniverseClient(
            token=SYNTHETIC_TOKEN,
            http_client=http_client,
        )
        bonds = source.list_base_bonds()
        dfas = source.list_dfas()
        assert bonds.response == {"instruments": []}
        assert bonds.instrument_status == "INSTRUMENT_STATUS_BASE"
        assert dfas.response == {"instruments": []}
        assert dfas.request_body == {}

    assert authorization_matches == [True, True]
    assert requests == [
        ("/rest" + BONDS_ROUTE, {"instrumentStatus": "INSTRUMENT_STATUS_BASE"}),
        ("/rest" + DFAS_ROUTE, {}),
    ]
    assert requests[0][1] == BONDS_BASE_REQUEST
    assert requests[1][1] == DFAS_REQUEST
    assert all(path != "/" for path, _ in requests)
    assert "ALL" not in json.dumps(requests)


def test_client_posts_to_documented_rest_proxy_routes() -> None:
    observed_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_urls.append(str(request.url))
        return httpx.Response(200, json={"instruments": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = TInvestInstrumentUniverseClient(
            token=SYNTHETIC_TOKEN,
            http_client=http_client,
        )
        client.list_base_bonds()
        client.list_dfas()

    assert observed_urls == [
        TINVEST_REST_BASE + BONDS_ROUTE,
        TINVEST_REST_BASE + DFAS_ROUTE,
    ]


def test_client_requires_explicit_nonblank_token_and_redacts_repr() -> None:
    with pytest.raises(ValueError):
        TInvestInstrumentUniverseClient(token="")
    with pytest.raises(ValueError):
        TInvestInstrumentUniverseClient(token=True)  # type: ignore[arg-type]

    source = TInvestInstrumentUniverseClient(token=SYNTHETIC_TOKEN)
    assert SYNTHETIC_TOKEN not in repr(source)
    assert "redacted" in repr(source)


def test_bond_normalization_preserves_source_identity_flags_and_tests() -> None:
    source_row = bond_row(uid="  bond-uid-1  ", isin=" RU000A000001 ")
    result = reduce([source_row])
    bond = result.bonds[0]

    assert result.bond_count == 1
    assert result.dfa_count == 0
    assert bond.uid == "  bond-uid-1  "
    assert bond.position_uid == "position-1"
    assert bond.asset_uid == "asset-1"
    assert bond.figi == "FIGI-1"
    assert bond.isin == " RU000A000001 "
    assert bond.ticker == "BOND1"
    assert bond.class_code == "TQCB"
    assert bond.buy_available is True
    assert bond.sell_available is False
    assert bond.api_trade_available is True
    assert bond.for_qual_investor is True
    assert bond.required_tests == ("test-a", "test-b")
    assert bond.required_tests_state == "SOURCE_VALUES"
    assert bond.floating_coupon is False
    assert bond.perpetual is False
    assert bond.amortizing is True
    assert bond.subordinated is False
    assert bond.source_universe == "BASE"
    assert bond.instrument_classification == "TINVEST_BASE_BOND"
    assert bond.listed_in_source_universe is True
    assert bond.availability_classification is TInvestAvailabilityClass.API_BUY_AVAILABLE


def test_bond_required_tests_distinguish_missing_empty_and_values() -> None:
    missing = reduce([bond_row(requiredTests=None)]).bonds[0]
    empty = reduce([bond_row(requiredTests=[])]).bonds[0]
    omitted = bond_row()
    omitted.pop("requiredTests")
    not_supplied = reduce([omitted]).bonds[0]

    assert missing.required_tests is None
    assert missing.required_tests_state == "NOT_SUPPLIED"
    assert empty.required_tests == ()
    assert empty.required_tests_state == "SOURCE_EMPTY"
    assert not_supplied.required_tests is None
    assert not_supplied.required_tests_state == "NOT_SUPPLIED"


def test_dfa_normalization_preserves_flags_and_does_not_invent_required_tests() -> None:
    source_row = dfa_row()
    result = reduce([], [source_row])
    dfa = result.dfas[0]

    assert result.dfa_count == 1
    assert dfa.uid == "dfa-uid-1"
    assert dfa.position_uid == "dfa-position-1"
    assert dfa.buy_available is False
    assert dfa.sell_available is True
    assert dfa.api_trade_available is True
    assert dfa.limit_order_available is True
    assert dfa.market_order_available is False
    assert dfa.bestprice_order_available is True
    assert dfa.for_iis is False
    assert dfa.for_qual_investor is True
    assert dfa.dfa_type == "debt_dfa"
    assert dfa.maturity_date == "2031-05-07T00:00:00Z"
    assert dfa.basic_assets == ({"uid": "underlying-1"},)
    assert dfa.required_tests is None
    assert dfa.required_tests_state == "NOT_SUPPLIED"
    assert dfa.availability_classification is TInvestAvailabilityClass.API_VISIBLE_NOT_BUYABLE

    # Economic values remain unconverted source evidence, including their nested shape.
    assert dfa.source_fields["nominal"] == {"units": "100", "nano": 25}
    assert dfa.source_fields["yieldToMaturity"] == {"units": "9", "nano": 0}
    assert dfa.source_fields["couponValue"] == {"units": "1", "nano": 500000000}
    assert dfa.source_fields["forecastYield"] == {"minValue": {"units": "8", "nano": 0}}


@pytest.mark.parametrize(
    "api_trade,buy,expected",
    [
        (True, True, TInvestAvailabilityClass.API_BUY_AVAILABLE),
        (True, False, TInvestAvailabilityClass.API_VISIBLE_NOT_BUYABLE),
        (False, True, TInvestAvailabilityClass.API_TRADE_UNAVAILABLE),
    ],
)
def test_availability_classification_keeps_qualification_orthogonal(
    api_trade: bool,
    buy: bool,
    expected: TInvestAvailabilityClass,
) -> None:
    bond = reduce(
        [
            bond_row(
                apiTradeAvailableFlag=api_trade,
                buyAvailableFlag=buy,
                forQualInvestorFlag=True,
            )
        ]
    ).bonds[0]
    dfa = reduce(
        [],
        [
            dfa_row(
                apiTradeAvailableFlag=api_trade,
                buyAvailableFlag=buy,
                forQualInvestorFlag=True,
            )
        ],
    ).dfas[0]

    assert bond.availability_classification is expected
    assert dfa.availability_classification is expected
    assert bond.for_qual_investor is True
    assert dfa.for_qual_investor is True


def test_missing_availability_flags_remain_unknown() -> None:
    bond_data = bond_row()
    del bond_data["apiTradeAvailableFlag"]
    del bond_data["buyAvailableFlag"]
    dfa_data = dfa_row()
    del dfa_data["apiTradeAvailableFlag"]
    del dfa_data["buyAvailableFlag"]

    bond = reduce([bond_data]).bonds[0]
    dfa = reduce([], [dfa_data]).dfas[0]

    assert bond.api_trade_available is None and bond.buy_available is None
    assert bond.availability_classification is None
    assert dfa.api_trade_available is None and dfa.buy_available is None
    assert dfa.availability_classification is None


@pytest.mark.parametrize("bad_uid", [None, "", "   ", 1, True])
def test_missing_or_malformed_uid_fails_closed(bad_uid: Any) -> None:
    with pytest.raises(TInvestUniverseSourceError) as error:
        reduce([bond_row(uid=bad_uid)])

    assert error.value.code is TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID


@pytest.mark.parametrize(
    "rows",
    [
        [bond_row("duplicate"), bond_row("duplicate")],
        [bond_row("duplicate", ticker="ONE"), bond_row("duplicate", ticker="TWO")],
    ],
)
def test_duplicate_uid_fails_closed_without_silent_overwrite(
    rows: list[dict[str, Any]],
) -> None:
    with pytest.raises(TInvestUniverseSourceError) as error:
        reduce(rows)

    assert error.value.code is TInvestUniverseFailureCode.SOURCE_IDENTITY_CONFLICT


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        "not-an-object",
        {},
        {"instruments": None},
        {"instruments": {}},
        {"instruments": [None]},
        {"instruments": ["bond"]},
        {"instruments": [{1: "non-string object key", "uid": "x"}]},
    ],
)
def test_malformed_top_level_or_instrument_shape_fails_closed(response: Any) -> None:
    with pytest.raises(TInvestUniverseSourceError) as error:
        TInvestInstrumentUniverseService.reduce_current_universe(
            TInvestBondsBaseResponse(response=response),
            TInvestDfasResponse(response={"instruments": []}),
        )

    assert error.value.code is TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID


def test_reducer_rejects_wrong_source_request_envelope() -> None:
    invalid_bonds_envelope = TInvestBondsBaseResponse.model_construct(
        method="Bonds",
        instrument_status="INSTRUMENT_STATUS_ALL",
        response={"instruments": [bond_row()]},
    )
    invalid_dfas_envelope = TInvestDfasResponse(
        request_body={"instrumentStatus": "INSTRUMENT_STATUS_BASE"},
        response={"instruments": [dfa_row()]},
    )
    empty_dfas_envelope = TInvestDfasResponse(response={"instruments": []})

    with pytest.raises(TInvestUniverseSourceError):
        TInvestInstrumentUniverseService.reduce_current_universe(
            invalid_bonds_envelope, empty_dfas_envelope
        )
    with pytest.raises(TInvestUniverseSourceError):
        TInvestInstrumentUniverseService.reduce_current_universe(
            TInvestBondsBaseResponse(response={"instruments": []}),
            invalid_dfas_envelope,
        )


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("buyAvailableFlag", "true"),
        ("sellAvailableFlag", 1),
        ("apiTradeAvailableFlag", 0),
        ("forQualInvestorFlag", None),
        ("floatingCouponFlag", "false"),
        ("perpetualFlag", 1),
        ("amortizationFlag", "1"),
        ("subordinatedFlag", []),
    ],
)
def test_malformed_bond_booleans_are_not_coerced(field: str, bad_value: Any) -> None:
    with pytest.raises(TInvestUniverseSourceError):
        reduce([bond_row(**{field: bad_value})])


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("buyAvailableFlag", "true"),
        ("sellAvailableFlag", 1),
        ("apiTradeAvailableFlag", None),
        ("limitOrderAvailableFlag", "false"),
        ("marketOrderAvailableFlag", 0),
        ("bestpriceOrderAvailableFlag", []),
        ("forIisFlag", "false"),
        ("forQualInvestorFlag", None),
    ],
)
def test_malformed_dfa_booleans_are_not_coerced(field: str, bad_value: Any) -> None:
    with pytest.raises(TInvestUniverseSourceError):
        reduce([], [dfa_row(**{field: bad_value})])


@pytest.mark.parametrize(
    "required_tests",
    ["test-a", ("test-a",), ["test-a", 1], [True]],
)
def test_malformed_required_tests_fails_closed(required_tests: Any) -> None:
    with pytest.raises(TInvestUniverseSourceError):
        reduce([bond_row(requiredTests=required_tests)])


@pytest.mark.parametrize("basic_assets", ["asset", {}, [1], [{1: "x"}]])
def test_malformed_dfa_basic_assets_fails_closed(basic_assets: Any) -> None:
    with pytest.raises(TInvestUniverseSourceError):
        reduce([], [dfa_row(basicAssets=basic_assets)])


@pytest.mark.parametrize(
    "field,value",
    [
        ("positionUid", 7),
        ("assetUid", "  "),
        ("figi", True),
        ("isin", []),
        ("ticker", 12),
        ("classCode", ""),
        ("lot", True),
        ("lot", "1"),
        ("currency", 3),
        ("maturityDate", 123),
    ],
)
def test_malformed_source_identifiers_and_fields_fail_closed(field: str, value: Any) -> None:
    with pytest.raises(TInvestUniverseSourceError):
        reduce([bond_row(**{field: value})])


def test_universe_is_sorted_by_uid_and_does_not_mutate_source_rows() -> None:
    source_rows = [bond_row("z-last"), bond_row("a-first")]
    original = json.loads(json.dumps(source_rows))

    result = reduce(source_rows)
    source_rows[0]["name"] = "caller mutation after reduction"

    assert [item.uid for item in result.bonds] == ["a-first", "z-last"]
    assert [item.uid for item in result.dfas] == []
    assert result.bonds[1].name == "Synthetic bond"
    assert original[0]["uid"] == "z-last"
    assert result.bond_count == 2


def test_contract_models_are_frozen_extra_forbid_and_current_only() -> None:
    bond = reduce([bond_row()]).bonds[0]
    dfa = reduce([], [dfa_row()]).dfas[0]

    with pytest.raises(ValidationError):
        bond.name = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        TInvestBondUniverseInstrument(**bond.model_dump(), unexpected="extra")
    with pytest.raises(ValidationError):
        TInvestDfaUniverseInstrument(**dfa.model_dump(), unexpected="extra")

    universe = reduce([bond_row()], [dfa_row()])
    assert universe.pit_ready is False
    assert universe.historical_universe_ready is False
    assert universe.personal_qualification_resolved is False
    assert universe.research_universe_preserved is True


@pytest.mark.parametrize(
    "status,expected_code",
    [
        (401, TInvestUniverseFailureCode.AUTHENTICATION_FAILED),
        (403, TInvestUniverseFailureCode.PERMISSION_DENIED),
        (429, TInvestUniverseFailureCode.RATE_LIMITED),
        (408, TInvestUniverseFailureCode.TIMEOUT),
        (500, TInvestUniverseFailureCode.TRANSIENT_SOURCE_ERROR),
        (502, TInvestUniverseFailureCode.TRANSIENT_SOURCE_ERROR),
        (503, TInvestUniverseFailureCode.TRANSIENT_SOURCE_ERROR),
        (504, TInvestUniverseFailureCode.TRANSIENT_SOURCE_ERROR),
        (400, TInvestUniverseFailureCode.REQUEST_REJECTED),
        (415, TInvestUniverseFailureCode.REQUEST_REJECTED),
    ],
)
def test_http_failures_are_sanitized(status: int, expected_code: TInvestUniverseFailureCode) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=SYNTHETIC_TOKEN)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = TInvestInstrumentUniverseClient(token=SYNTHETIC_TOKEN, http_client=http_client)
        with pytest.raises(TInvestUniverseSourceError) as error:
            client.list_base_bonds()

    assert error.value.code is expected_code
    assert error.value.http_status == status
    assert SYNTHETIC_TOKEN not in str(error.value)
    assert SYNTHETIC_TOKEN not in repr(error.value)


def test_timeout_error_is_sanitized() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(SYNTHETIC_TOKEN)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = TInvestInstrumentUniverseClient(token=SYNTHETIC_TOKEN, http_client=http_client)
        with pytest.raises(TInvestUniverseSourceError) as error:
            client.list_dfas()

    assert error.value.code is TInvestUniverseFailureCode.TIMEOUT
    assert SYNTHETIC_TOKEN not in str(error.value)
    assert SYNTHETIC_TOKEN not in repr(error.value)


def test_transient_transport_error_is_sanitized() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(SYNTHETIC_TOKEN)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = TInvestInstrumentUniverseClient(token=SYNTHETIC_TOKEN, http_client=http_client)
        with pytest.raises(TInvestUniverseSourceError) as error:
            client.list_base_bonds()

    assert error.value.code is TInvestUniverseFailureCode.TRANSIENT_SOURCE_ERROR
    assert SYNTHETIC_TOKEN not in str(error.value)
    assert SYNTHETIC_TOKEN not in repr(error.value)


def test_malformed_json_error_is_sanitized() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"malformed {SYNTHETIC_TOKEN}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = TInvestInstrumentUniverseClient(token=SYNTHETIC_TOKEN, http_client=http_client)
        with pytest.raises(TInvestUniverseSourceError) as error:
            client.list_dfas()

    assert error.value.code is TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID
    assert SYNTHETIC_TOKEN not in str(error.value)
    assert SYNTHETIC_TOKEN not in repr(error.value)


def test_reducer_errors_are_sanitized_and_uid_conflict_has_own_category() -> None:
    with pytest.raises(TInvestUniverseSourceError) as malformed:
        reduce([bond_row(uid=SYNTHETIC_TOKEN), bond_row(uid=SYNTHETIC_TOKEN)])

    assert malformed.value.code is TInvestUniverseFailureCode.SOURCE_IDENTITY_CONFLICT
    assert SYNTHETIC_TOKEN not in str(malformed.value)
    assert SYNTHETIC_TOKEN not in repr(malformed.value)


def test_runtime_surface_has_no_broker_writes_generic_rpc_or_environment_token() -> None:
    from app.schemas import tinvest_instrument_universe as schema_module
    from app.services import (
        tinvest_instrument_universe_client as client_module,
        tinvest_instrument_universe_service as service_module,
    )

    paths = [
        Path(schema_module.__file__),
        Path(client_module.__file__),
        Path(service_module.__file__),
    ]
    forbidden = {
        "PostOrder",
        "PostOrderAsync",
        "ReplaceOrder",
        "CancelOrder",
        "PostStopOrder",
        "CancelStopOrder",
        "withdraw",
        "pay_in",
        "generic_rpc",
        "generic_post",
        "invoke_method",
        "call_method_by_name",
        "GetAccounts",
        "GetPortfolio",
    }
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        }
        attributes = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not (forbidden & names)
        assert not (forbidden & attributes)
        assert "os" not in imports
        assert "sqlalchemy" not in imports

    client_tree = ast.parse(paths[1].read_text(encoding="utf-8"))
    public_methods = {
        node.name
        for node in ast.walk(client_tree)
        if isinstance(node, ast.FunctionDef)
        and node.name.startswith("list_")
    }
    assert public_methods == {"list_base_bonds", "list_dfas"}
