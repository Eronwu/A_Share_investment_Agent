import { NextResponse } from "next/server";
import { readFileSync, writeFileSync, renameSync } from "fs";
import { join } from "path";

const DATA_DIR = join(process.cwd(), "..", "src", "data");
const PLANS_PATH = join(DATA_DIR, "trade_plans.json");
const CONFIG_PATH = join(DATA_DIR, "monitor_config.json");
const MARKET_PATH = join(DATA_DIR, "market_data.json");

/* ── Types ── */

interface Order {
  id: string;
  side: "buy" | "sell";
  op: ">=" | "<=";
  price: number;
  shares: number | null;
  volume_min: number | null;
  consecutive_days: number | null;
  trailing: { pct: number; watermark: number | null; active: boolean } | null;
  label: string;
  triggered: boolean;
  triggered_at: string | null;
}

interface TradePlan {
  name: string;
  symbol: string;
  status: "active" | "paused";
  scope: "real" | "sim";
  created_at: string;
  orders: Order[];
}

interface PlansFile {
  plans: Record<string, TradePlan>;
}

interface PlanPosition {
  cost: number | null;
  shares: number | null;
  price: number | null;
  change_pct: number | null;
  name: string;
}

/* ── Readers ── */

function readPlans(): PlansFile {
  try {
    const raw = readFileSync(PLANS_PATH, "utf-8");
    return JSON.parse(raw);
  } catch {
    return { plans: {} };
  }
}

function writePlans(data: PlansFile) {
  const tmp = PLANS_PATH + ".tmp";
  writeFileSync(tmp, JSON.stringify(data, null, 2) + "\n", "utf-8");
  renameSync(tmp, PLANS_PATH);
}

function readConfig(): { watchlist: Record<string, { name: string; type?: string; cost?: number | null; shares?: number | null; lot?: number }> } {
  try {
    const raw = readFileSync(CONFIG_PATH, "utf-8");
    return JSON.parse(raw);
  } catch {
    return { watchlist: {} };
  }
}

function readMarket(): { services: { id: string; name: string; price: number; change: number }[] } {
  try {
    const raw = readFileSync(MARKET_PATH, "utf-8");
    return JSON.parse(raw);
  } catch {
    return { services: [] };
  }
}

/* ── GET ── */

export async function GET() {
  try {
    const { plans } = readPlans();
    const config = readConfig();
    const market = readMarket();

    const priceMap = new Map<string, { price: number; change: number; name: string }>();
    for (const s of market.services) {
      priceMap.set(s.id, { price: s.price, change: s.change, name: s.name });
    }

    const result: Record<string, TradePlan & { position: PlanPosition | null; lot_size: number | null }> = {};

    for (const [id, plan] of Object.entries(plans)) {
      const wl = config.watchlist[plan.symbol];
      const mkt = priceMap.get(plan.symbol);

      const position: PlanPosition | null = wl
        ? {
            cost: wl.cost ?? null,
            shares: wl.shares ?? null,
            price: mkt?.price ?? null,
            change_pct: mkt?.change ?? null,
            name: wl.name || mkt?.name || plan.symbol,
          }
        : null;

      const lot_size = wl?.lot ?? null;

      result[id] = { ...plan, position, lot_size };
    }

    return NextResponse.json({ plans: result });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

/* ── POST ── */

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { action } = body;
    const data = readPlans();

    switch (action) {
      /* ── create ── */
      case "create": {
        const { id, plan } = body as { id: string; plan: Partial<TradePlan> };
        if (!id || !plan?.symbol || !plan?.name) {
          return NextResponse.json({ success: false, message: "Missing id, symbol, or name" }, { status: 400 });
        }
        if (data.plans[id]) {
          return NextResponse.json({ success: false, message: `Plan ${id} already exists` }, { status: 400 });
        }

        // validate orders
        if (plan.orders) {
          for (const o of plan.orders) {
            if (o.side === "buy" && (o.shares == null || o.shares <= 0)) {
              return NextResponse.json({ success: false, message: `Buy order ${o.id} needs shares > 0` }, { status: 400 });
            }
            if (o.side === "sell" && (o.shares == null || o.shares <= 0)) {
              return NextResponse.json({ success: false, message: `Sell order ${o.id} needs shares > 0` }, { status: 400 });
            }
          }
        }

        const newPlan: TradePlan = {
          name: plan.name,
          symbol: plan.symbol,
          status: plan.status || "active",
          scope: plan.scope || "real",
          created_at: plan.created_at || new Date().toISOString().slice(0, 10),
          orders: plan.orders || [],
        };

        data.plans[id] = newPlan;
        writePlans(data);
        return NextResponse.json({ success: true, message: `Created plan ${id}` });
      }

      /* ── update ── */
      case "update": {
        const { id, updates } = body as { id: string; updates: Partial<TradePlan> };
        if (!id || !data.plans[id]) {
          return NextResponse.json({ success: false, message: `Plan ${id} not found` }, { status: 400 });
        }

        const plan = data.plans[id];

        if (updates.name !== undefined) plan.name = updates.name;
        if (updates.status !== undefined) plan.status = updates.status;
        if (updates.scope !== undefined) plan.scope = updates.scope;

        // orders replacement
        if (updates.orders !== undefined) {
          // validate
          for (const o of updates.orders) {
            if (o.side === "buy" && (o.shares == null || o.shares <= 0)) {
              return NextResponse.json({ success: false, message: `Buy order ${o.id} needs shares > 0` }, { status: 400 });
            }
            if (o.side === "sell" && (o.shares == null || o.shares <= 0)) {
              return NextResponse.json({ success: false, message: `Sell order ${o.id} needs shares > 0` }, { status: 400 });
            }
          }
          plan.orders = updates.orders;
        }

        data.plans[id] = plan;
        writePlans(data);
        return NextResponse.json({ success: true, message: `Updated plan ${id}` });
      }

      /* ── delete ── */
      case "delete": {
        const { id } = body as { id: string };
        if (!id || !data.plans[id]) {
          return NextResponse.json({ success: false, message: `Plan ${id} not found` }, { status: 400 });
        }
        delete data.plans[id];
        writePlans(data);
        return NextResponse.json({ success: true, message: `Deleted plan ${id}` });
      }

      /* ── toggle (active/paused) ── */
      case "toggle": {
        const { id } = body as { id: string };
        if (!id || !data.plans[id]) {
          return NextResponse.json({ success: false, message: `Plan ${id} not found` }, { status: 400 });
        }
        const plan = data.plans[id];
        plan.status = plan.status === "active" ? "paused" : "active";
        data.plans[id] = plan;
        writePlans(data);
        return NextResponse.json({ success: true, message: `${plan.status === "active" ? "Activated" : "Paused"} plan ${id}` });
      }

      /* ── reset (un-trigger an order) ── */
      case "reset": {
        const { id, order_id } = body as { id: string; order_id: string };
        if (!id || !data.plans[id]) {
          return NextResponse.json({ success: false, message: `Plan ${id} not found` }, { status: 400 });
        }
        const plan = data.plans[id];
        const order = plan.orders.find((o) => o.id === order_id);
        if (!order) {
          return NextResponse.json({ success: false, message: `Order ${order_id} not found in plan ${id}` }, { status: 400 });
        }
        order.triggered = false;
        order.triggered_at = null;
        data.plans[id] = plan;
        writePlans(data);
        return NextResponse.json({ success: true, message: `Reset order ${order_id} in plan ${id}` });
      }

      default:
        return NextResponse.json({ success: false, message: `Unknown action: ${action}` }, { status: 400 });
    }
  } catch (e) {
    return NextResponse.json({ success: false, message: String(e) }, { status: 500 });
  }
}
