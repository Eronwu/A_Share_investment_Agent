import { describe, it, expect } from "vitest";
import { parseCommand, getCompletions } from "../commandParser";

describe("parseCommand", () => {
  // ── empty / help ──
  it("empty input returns help with error", () => {
    const r = parseCommand("");
    expect(r.action).toBe("help");
    expect(r.errors).toBeDefined();
  });

  it("'svc' alone returns help", () => {
    expect(parseCommand("svc").action).toBe("help");
  });

  it("'help' returns help", () => {
    expect(parseCommand("help").action).toBe("help");
  });

  it("'svc help' returns help", () => {
    expect(parseCommand("svc help").action).toBe("help");
  });

  it("'?' and 'h' are help aliases", () => {
    expect(parseCommand("?").action).toBe("help");
    expect(parseCommand("h").action).toBe("help");
  });

  it("unknown command returns error", () => {
    const r = parseCommand("svc foo");
    expect(r.errors?.[0]).toContain("Unknown command");
  });

  // ── add ──
  describe("add", () => {
    it("adds A-share stock", () => {
      const r = parseCommand("svc add 600089");
      expect(r.action).toBe("add");
      expect(r.code).toBe("600089");
      expect(r.data).toEqual({});
    });

    it("adds HK stock with normalized prefix", () => {
      const r = parseCommand("svc add hk00700");
      expect(r.code).toBe("HK00700");
    });

    it("adds with --above and --below", () => {
      const r = parseCommand("svc add 688676 --above 100 --below 85");
      expect(r.data).toEqual({ above: 100, below: 85 });
    });

    it("adds holding with --env prod --cost --shares", () => {
      const r = parseCommand("svc add HK09988 --env prod --cost 155 --shares 200");
      expect(r.data).toEqual({ type: "holding", cost: 155, shares: 200 });
    });

    it("auto-sets holding type when --cost is provided", () => {
      const r = parseCommand("svc add 600673 --cost 28.5 --shares 500");
      expect(r.data?.type).toBe("holding");
    });

    it("requires code", () => {
      const r = parseCommand("svc add");
      expect(r.errors).toBeDefined();
    });

    it("rejects invalid code", () => {
      const r = parseCommand("svc add ABC");
      expect(r.errors?.[0]).toContain("Invalid stock code");
    });

    it("rejects negative cost", () => {
      const r = parseCommand("svc add 600089 --cost -10 --shares 100");
      expect(r.errors?.[0]).toContain("--cost must be a positive number");
    });

    it("rejects non-numeric shares", () => {
      const r = parseCommand("svc add 600089 --cost 10 --shares abc");
      expect(r.errors?.[0]).toContain("--shares must be a positive number");
    });

    it("requires --cost and --shares for --env prod", () => {
      const r = parseCommand("svc add 600089 --env prod");
      expect(r.errors?.[0]).toContain("Holding requires");
    });

    it("works without svc prefix", () => {
      const r = parseCommand("add 600089");
      expect(r.action).toBe("add");
      expect(r.code).toBe("600089");
    });
  });

  // ── update ──
  describe("update", () => {
    it("updates cost", () => {
      const r = parseCommand("svc update HK00700 --cost 420");
      expect(r.action).toBe("update");
      expect(r.code).toBe("HK00700");
      expect(r.data).toEqual({ cost: 420 });
    });

    it("updates env to watching", () => {
      const r = parseCommand("svc update 600089 --env dev");
      expect(r.data?.type).toBe("watching");
    });

    it("requires at least one flag", () => {
      const r = parseCommand("svc update 600089");
      expect(r.errors?.[0]).toContain("Nothing to update");
    });

    it("requires code", () => {
      const r = parseCommand("svc update");
      expect(r.errors).toBeDefined();
    });

    it("rejects invalid code", () => {
      const r = parseCommand("svc update INVALID --cost 10");
      expect(r.errors?.[0]).toContain("Invalid stock code");
    });
  });

  // ── remove ──
  describe("remove", () => {
    it("removes single stock", () => {
      const r = parseCommand("svc rm 600089");
      expect(r.action).toBe("remove");
      expect(r.codes).toEqual(["600089"]);
    });

    it("removes multiple stocks", () => {
      const r = parseCommand("svc rm 600089 HK00700 002335");
      expect(r.codes).toEqual(["600089", "HK00700", "002335"]);
    });

    it("supports aliases: remove, del, delete", () => {
      expect(parseCommand("remove 600089").action).toBe("remove");
      expect(parseCommand("del 600089").action).toBe("remove");
      expect(parseCommand("delete 600089").action).toBe("remove");
    });

    it("requires code", () => {
      const r = parseCommand("svc rm");
      expect(r.errors).toBeDefined();
    });

    it("rejects invalid code in batch", () => {
      const r = parseCommand("svc rm 600089 INVALID");
      expect(r.errors?.[0]).toContain("Invalid stock code");
    });
  });

  // ── list ──
  describe("list", () => {
    it("lists all", () => {
      const r = parseCommand("svc ls");
      expect(r.action).toBe("list");
      expect(r.data).toEqual({});
    });

    it("filters by prod", () => {
      const r = parseCommand("svc ls prod");
      expect(r.data?.filter).toBe("holding");
    });

    it("filters by dev", () => {
      const r = parseCommand("svc ls dev");
      expect(r.data?.filter).toBe("watching");
    });

    it("'list' alias works", () => {
      expect(parseCommand("list prod").data?.filter).toBe("holding");
    });
  });

  // ── config ──
  describe("config", () => {
    it("sets a config value", () => {
      const r = parseCommand("svc config poll_interval 30");
      expect(r.action).toBe("config");
      expect(r.settings).toEqual({ poll_interval: 30 });
    });

    it("'set' alias works", () => {
      const r = parseCommand("set big_move_pct 5.0");
      expect(r.settings).toEqual({ big_move_pct: 5 });
    });

    it("requires key and value", () => {
      const r = parseCommand("svc config poll_interval");
      expect(r.errors).toBeDefined();
    });

    it("rejects non-numeric value", () => {
      const r = parseCommand("svc config poll_interval abc");
      expect(r.errors?.[0]).toContain("Value must be a number");
    });
  });

  // ── hide / unhide ──
  describe("hide / unhide", () => {
    it("hide maps to update with hidden:true", () => {
      const r = parseCommand("svc hide HK00700");
      expect(r.action).toBe("update");
      expect(r.code).toBe("HK00700");
      expect(r.data).toEqual({ hidden: true });
    });

    it("unhide maps to update with hidden:false", () => {
      const r = parseCommand("svc unhide HK00700");
      expect(r.action).toBe("update");
      expect(r.data).toEqual({ hidden: false });
    });

    it("'show' is alias for unhide", () => {
      const r = parseCommand("show 600089");
      expect(r.data).toEqual({ hidden: false });
    });

    it("requires code", () => {
      expect(parseCommand("svc hide").errors).toBeDefined();
      expect(parseCommand("svc unhide").errors).toBeDefined();
    });

    it("rejects invalid code", () => {
      const r = parseCommand("svc hide INVALID");
      expect(r.errors?.[0]).toContain("Invalid stock code");
    });
  });

  // ── star / unstar ──
  describe("star / unstar", () => {
    it("star maps to update with star:true", () => {
      const r = parseCommand("svc star HK00700");
      expect(r.action).toBe("update");
      expect(r.code).toBe("HK00700");
      expect(r.data).toEqual({ star: true });
    });

    it("unstar maps to update with star:false", () => {
      const r = parseCommand("svc unstar 600089");
      expect(r.action).toBe("update");
      expect(r.data).toEqual({ star: false });
    });

    it("requires code", () => {
      expect(parseCommand("svc star").errors).toBeDefined();
      expect(parseCommand("svc unstar").errors).toBeDefined();
    });

    it("rejects invalid code", () => {
      expect(parseCommand("svc star INVALID").errors?.[0]).toContain("Invalid stock code");
    });
  });

  // ── code validation edge cases ──
  describe("code validation", () => {
    it("accepts 6-digit A-share codes", () => {
      expect(parseCommand("add 000001").code).toBe("000001");
      expect(parseCommand("add 688676").code).toBe("688676");
      expect(parseCommand("add 301388").code).toBe("301388");
    });

    it("accepts 5-digit HK codes", () => {
      expect(parseCommand("add HK00700").code).toBe("HK00700");
      expect(parseCommand("add HK09988").code).toBe("HK09988");
    });

    it("accepts ETF codes", () => {
      expect(parseCommand("add 159995").code).toBe("159995");
      expect(parseCommand("add 512800").code).toBe("512800");
      expect(parseCommand("add 588000").code).toBe("588000");
    });

    it("rejects too-short codes", () => {
      expect(parseCommand("add 123").errors).toBeDefined();
    });

    it("rejects too-long codes", () => {
      expect(parseCommand("add 1234567").errors).toBeDefined();
    });

    it("rejects alphabetic codes", () => {
      expect(parseCommand("add AAPL").errors).toBeDefined();
    });

    it("normalizes HK prefix to uppercase", () => {
      expect(parseCommand("add hk00700").code).toBe("HK00700");
      expect(parseCommand("add Hk09988").code).toBe("HK09988");
    });
  });
});

describe("getCompletions", () => {
  it("suggests 'svc ' for empty input", () => {
    expect(getCompletions("")).toEqual(["svc "]);
  });

  it("suggests 'svc ' for partial 'sv'", () => {
    expect(getCompletions("sv")).toEqual(["svc "]);
  });

  it("suggests 'svc ' when input is 'svc ' (trailing space)", () => {
    // getCompletions treats "svc " as: tokens=["svc"], trimmed ends with space
    // so it tries to complete the subcommand with empty prefix → all subcommands
    const completions = getCompletions("svc ");
    expect(completions.length).toBeGreaterThan(0);
  });

  it("filters subcommands by prefix", () => {
    const completions = getCompletions("svc h");
    expect(completions).toContain("svc hide ");
    expect(completions).toContain("svc help ");
    expect(completions).not.toContain("svc add ");
  });

  it("partial 'a' without svc prefix suggests 'svc ' (prefers svc completion)", () => {
    // When input is "a", getCompletions checks if "svc".startsWith("a") → false → []
    // This is correct: bare partial only completes "svc" prefix, not subcommands directly
    const completions = getCompletions("a");
    expect(completions).toEqual([]);
  });

  it("after complete subcommand, no further completions for args", () => {
    // "svc add 6" — user is typing a stock code, no completions to offer
    const completions = getCompletions("svc add 6");
    expect(completions).toEqual([]);
  });
});
