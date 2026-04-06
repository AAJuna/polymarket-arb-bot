"""
Trade Executor — places, tracks, and cancels orders on the Polymarket CLOB.
Handles L1/L2 auth, paper trading simulation, stale order cleanup, and
Web3 position redemption for settled markets.
"""

from __future__ import annotations

import math
import threading
import time
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

import config
from logger_setup import get_logger
from utils import utcnow

if TYPE_CHECKING:
    from arbitrage import Opportunity

logger = get_logger(__name__)


class Executor:
    def __init__(self):
        self._open_orders: dict[str, dict] = {}   # order_id → metadata
        self._lock = threading.Lock()
        self._client = None
        self._w3 = None
        self._account = None

        if not config.PAPER_TRADING:
            self._init_clob_client()
            self._init_web3()

        # Background thread for stale order cancellation
        self._stale_thread = threading.Thread(
            target=self._stale_order_loop, daemon=True, name="stale-order-cleaner"
        )
        self._stale_thread.start()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_clob_client(self) -> None:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.constants import POLYGON

            kwargs = {
                "key": config.PRIVATE_KEY,
                "chain_id": config.CHAIN_ID,
            }
            if config.SIGNATURE_TYPE == 1 and config.FUNDER_ADDRESS:
                kwargs["signature_type"] = 1
                kwargs["funder"] = config.FUNDER_ADDRESS

            self._client = ClobClient(config.POLYMARKET_HOST, **kwargs)
            creds = self._client.create_or_derive_api_creds()
            self._client.set_api_creds(creds)
            logger.info("CLOB client initialised (L1 + L2 auth)")
        except Exception as e:
            logger.error(f"CLOB client init failed: {e}", exc_info=True)
            raise

    def _init_web3(self) -> None:
        try:
            from web3 import Web3
            rpc = "https://polygon-rpc.com"
            self._w3 = Web3(Web3.HTTPProvider(rpc))
            self._account = self._w3.eth.account.from_key(config.PRIVATE_KEY)
            logger.info(f"Web3 initialised — wallet {self._account.address[:10]}...")
        except Exception as e:
            logger.warning(f"Web3 init failed (redemption disabled): {e}")

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_tick_size(raw_tick_size: float) -> float:
        allowed_ticks = (0.1, 0.01, 0.001, 0.0001)
        try:
            tick_size = float(raw_tick_size)
        except Exception:
            return 0.01

        for allowed in allowed_ticks:
            if abs(tick_size - allowed) < 1e-9:
                return allowed

        return min(allowed_ticks, key=lambda allowed: abs(allowed - tick_size))

    @staticmethod
    def _format_tick_size(tick_size: float) -> str:
        if tick_size >= 0.1:
            return "0.1"
        if tick_size >= 0.01:
            return "0.01"
        if tick_size >= 0.001:
            return "0.001"
        return "0.0001"

    @classmethod
    def _round_price_up(cls, price: float, tick_size: float) -> float:
        ticks = math.ceil((max(price, tick_size) - 1e-12) / tick_size)
        precision = len(cls._format_tick_size(tick_size).split(".")[-1])
        return round(ticks * tick_size, precision)

    def _build_order_plan(
        self,
        opp: "Opportunity",
        size_dollars: float,
        best_ask: float,
    ) -> tuple[Optional[dict], str]:
        raw_market = getattr(opp, "raw_data", None)
        tick_size = self._normalize_tick_size(
            getattr(raw_market, "order_price_min_tick_size", 0.01)
        )
        max_price = max(tick_size, round(1.0 - tick_size, 4))
        if best_ask > max_price:
            return None, f"ask_above_max_price ({best_ask:.4f} > {max_price:.4f})"
        exec_price = self._round_price_up(best_ask, tick_size)
        if exec_price <= 0:
            return None, "invalid_exec_price"

        shares = round(size_dollars / exec_price, 4) if exec_price > 0 else 0.0
        if shares <= 0:
            return None, "size_zero"

        order_min_size = float(getattr(raw_market, "order_min_size", 0.0) or 0.0)
        if order_min_size > 0 and shares < order_min_size:
            return None, f"order_min_size ({shares:.4f} < {order_min_size:.4f})"

        return {
            "shares": shares,
            "exec_price": exec_price,
            "fill_cost": round(shares * exec_price, 6),
            "tick_size": tick_size,
            "neg_risk": bool(getattr(raw_market, "neg_risk", False)),
            # Let the official SDK resolve the canonical market fee bps from CLOB.
            "fee_rate_bps": 0,
        }, ""

    def place_order(self, opp: "Opportunity", size_dollars: float) -> Optional[dict]:
        """Place a BUY order. Returns order result dict or None on failure."""
        import scanner as market_scanner

        is_open, _, reason = market_scanner.verify_market_open(opp.condition_id, opp.question)
        if not is_open:
            logger.info(
                f"Skipping {opp.market_id}: market not open ({reason}) | "
                f"{opp.question[:50]}"
            )
            return None

        if config.PAPER_TRADING:
            plan, reason = self._build_order_plan(opp, size_dollars, opp.price)
            if plan is None:
                logger.info(f"Skipping {opp.market_id}: {reason}")
                return None
            return self._simulate_order(opp, plan)

        if self._client is None:
            logger.error("CLOB client not initialised")
            return None

        try:
            from py_clob_client.clob_types import (
                OrderArgs,
                OrderType,
                PartialCreateOrderOptions,
            )
            from py_clob_client.order_builder.constants import BUY

            # Live price check
            book = self._client.get_order_book(opp.token_id)
            asks = book.asks if book else []
            if not asks:
                logger.warning(f"No asks in orderbook for {opp.token_id}")
                return None

            best_ask = float(asks[0].price)
            if abs(best_ask - opp.price) > 0.03:
                logger.warning(
                    f"Price moved too much for {opp.market_id}: "
                    f"expected {opp.price:.3f}, got {best_ask:.3f} — skipping"
                )
                return None

            plan, reason = self._build_order_plan(opp, size_dollars, best_ask)
            if plan is None:
                logger.info(f"Skipping {opp.market_id}: {reason}")
                return None

            order_args = OrderArgs(
                token_id=opp.token_id,
                price=plan["exec_price"],
                size=plan["shares"],
                side=BUY,
                fee_rate_bps=plan["fee_rate_bps"],
            )
            order_options = PartialCreateOrderOptions(
                tick_size=self._format_tick_size(plan["tick_size"]),
                neg_risk=True if plan["neg_risk"] else None,
            )
            signed = self._client.create_order(order_args, order_options)
            resp = self._client.post_order(signed, OrderType.GTC)

            if resp and resp.get("success"):
                order_id = resp.get("orderID", str(uuid4()))
                with self._lock:
                    self._open_orders[order_id] = {
                        "opportunity": opp,
                        "placed_at": utcnow(),
                        "size": plan["shares"],
                        "price": plan["exec_price"],
                        "token_id": opp.token_id,
                    }
                logger.info(
                    f"Order placed: {order_id[:12]}… | "
                    f"{opp.question[:40]} | {opp.side} | "
                    f"{plan['shares']:.4f} shares @ ${plan['exec_price']:.4f}"
                )
                return {
                    "success": True,
                    "orderID": order_id,
                    "fill_price": plan["exec_price"],
                    "fill_size": plan["shares"],
                    "fill_cost": plan["fill_cost"],
                    "simulated": False,
                }
            else:
                logger.warning(f"Order rejected for {opp.market_id}: {resp}")
                return None

        except Exception as e:
            logger.error(f"Order placement failed for {opp.market_id}: {e}", exc_info=True)
            return None

    def _simulate_order(self, opp: "Opportunity", plan: dict) -> dict:
        """Paper trade: simulate an immediate fill at current price."""
        order_id = f"PAPER_{uuid4().hex[:8]}"
        with self._lock:
            self._open_orders[order_id] = {
                "opportunity": opp,
                "placed_at": utcnow(),
                "size": plan["shares"],
                "price": plan["exec_price"],
                "token_id": opp.token_id,
            }
        logger.debug(
            f"[PAPER] Simulated fill: {opp.question[:40]} | "
            f"{opp.side} | {plan['shares']:.4f} shares @ ${plan['exec_price']:.4f} | "
            f"cost=${plan['fill_cost']:.2f}"
        )
        return {
            "success": True,
            "orderID": order_id,
            "fill_price": plan["exec_price"],
            "fill_size": plan["shares"],
            "fill_cost": plan["fill_cost"],
            "simulated": True,
        }

    # ------------------------------------------------------------------
    # Stale order cleanup
    # ------------------------------------------------------------------

    def _stale_order_loop(self) -> None:
        """Background thread: cancel orders unfilled for > STALE_ORDER_TIMEOUT seconds."""
        while True:
            time.sleep(5)
            try:
                self._cancel_stale_orders()
            except Exception as e:
                logger.debug(f"Stale order loop error: {e}")

    def _cancel_stale_orders(self) -> None:
        with self._lock:
            order_ids = list(self._open_orders.keys())

        for order_id in order_ids:
            with self._lock:
                meta = self._open_orders.get(order_id)
            if not meta:
                continue

            age = (utcnow() - meta["placed_at"]).total_seconds()
            if age < config.STALE_ORDER_TIMEOUT:
                continue

            if order_id.startswith("PAPER_"):
                with self._lock:
                    self._open_orders.pop(order_id, None)
                logger.debug(f"Removed stale paper order {order_id}")
                continue

            try:
                if self._client:
                    self._client.cancel(order_id)
                with self._lock:
                    self._open_orders.pop(order_id, None)
                logger.info(f"Cancelled stale order {order_id[:12]}… (age={age:.0f}s)")
            except Exception as e:
                logger.warning(f"Failed to cancel stale order {order_id}: {e}")

    def cancel_all(self) -> None:
        """Cancel all open orders — called on shutdown."""
        if config.PAPER_TRADING:
            with self._lock:
                self._open_orders.clear()
            return
        try:
            if self._client:
                self._client.cancel_all()
                logger.info("All open orders cancelled on shutdown")
        except Exception as e:
            logger.warning(f"cancel_all failed: {e}")

    # ------------------------------------------------------------------
    # Position redemption (Web3)
    # ------------------------------------------------------------------

    def redeem_position(self, condition_id: str) -> bool:
        """Redeem a resolved winning position via the CTF contract."""
        if config.PAPER_TRADING:
            logger.debug(f"[PAPER] Skipping redemption for {condition_id}")
            return True

        if self._w3 is None or self._account is None:
            logger.warning("Web3 not available — cannot redeem position")
            return False

        try:
            ctf_abi = [
                {
                    "name": "redeemPositions",
                    "type": "function",
                    "inputs": [
                        {"name": "collateralToken", "type": "address"},
                        {"name": "parentCollectionId", "type": "bytes32"},
                        {"name": "conditionId", "type": "bytes32"},
                        {"name": "indexSets", "type": "uint256[]"},
                    ],
                    "outputs": [],
                    "stateMutability": "nonpayable",
                }
            ]

            ctf = self._w3.eth.contract(
                address=self._w3.to_checksum_address(config.CTF_ADDRESS),
                abi=ctf_abi,
            )

            tx = ctf.functions.redeemPositions(
                self._w3.to_checksum_address(config.USDC_ADDRESS),
                b"\x00" * 32,                              # parentCollectionId
                bytes.fromhex(condition_id.lstrip("0x")),  # conditionId
                [1, 2],                                    # indexSets (YES=1, NO=2)
            ).build_transaction({
                "from": self._account.address,
                "nonce": self._w3.eth.get_transaction_count(self._account.address),
                "gas": 200_000,
                "gasPrice": self._w3.eth.gas_price,
            })

            signed_tx = self._account.sign_transaction(tx)
            tx_hash = self._w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

            success = receipt.status == 1
            logger.info(
                f"Redemption {'succeeded' if success else 'FAILED'}: "
                f"conditionId={condition_id[:16]}… tx={tx_hash.hex()[:16]}…"
            )
            return success

        except Exception as e:
            logger.error(f"Redemption failed for {condition_id}: {e}", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Balance query
    # ------------------------------------------------------------------

    def get_account_address(self) -> Optional[str]:
        """Return the best address to use for live account reconciliation."""
        if config.SIGNATURE_TYPE == 1 and config.FUNDER_ADDRESS:
            return config.FUNDER_ADDRESS
        if self._client is not None:
            try:
                return self._client.get_address()
            except Exception:
                pass
        if self._account is not None:
            return self._account.address
        return None

    def get_usdc_balance(self) -> Optional[float]:
        """Return current USDC balance from CLOB (in dollars)."""
        if config.PAPER_TRADING or self._client is None:
            return None
        try:
            resp = self._client.get_balance_allowance({"asset_type": "COLLATERAL"})
            # USDC has 6 decimals on Polygon
            return float(resp.get("balance", 0)) / 1e6
        except Exception as e:
            logger.warning(f"Balance query failed: {e}")
            return None
