export interface ParsedCommand {
  action: "add" | "update" | "remove" | "list" | "config" | "help";
  code?: string;
  codes?: string[];
  data?: Record<string, unknown>;
  settings?: Record<string, number>;
  errors?: string[];
}

const CODE_RE = /^(HK)?\d{5,6}$/i;

function tokenize(input: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let inQuote = false;
  for (const ch of input) {
    if (ch === '"' || ch === "'") {
      inQuote = !inQuote;
    } else if (ch === " " && !inQuote) {
      if (current) tokens.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  if (current) tokens.push(current);
  return tokens;
}

function parseFlags(tokens: string[]): { args: string[]; flags: Record<string, string> } {
  const args: string[] = [];
  const flags: Record<string, string> = {};

  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.startsWith("--")) {
      const key = t.slice(2);
      const next = tokens[i + 1];
      if (next && !next.startsWith("--")) {
        flags[key] = next;
        i++;
      } else {
        flags[key] = "true";
      }
    } else {
      args.push(t);
    }
  }

  return { args, flags };
}

function validateCode(code: string): string | null {
  if (!CODE_RE.test(code)) {
    return `Invalid stock code: ${code} (expected 6-digit number or HK+5-6 digits)`;
  }
  return null;
}

function normalizeCode(code: string): string {
  if (code.toUpperCase().startsWith("HK")) {
    return "HK" + code.slice(2);
  }
  return code;
}

export function parseCommand(input: string): ParsedCommand {
  const trimmed = input.trim();
  if (!trimmed) {
    return { action: "help", errors: ["Empty command"] };
  }

  const tokens = tokenize(trimmed);
  const prefix = tokens[0]?.toLowerCase();

  // accept both "svc xxx" and bare "xxx" commands
  let rest: string[];
  if (prefix === "svc") {
    rest = tokens.slice(1);
  } else {
    rest = tokens;
  }

  if (rest.length === 0) {
    return { action: "help" };
  }

  const sub = rest[0].toLowerCase();
  const { args, flags } = parseFlags(rest.slice(1));

  switch (sub) {
    case "add": {
      if (args.length === 0) {
        return { action: "add", errors: ["Usage: svc add <code> [--env prod] [--cost N] [--shares N] [--above N] [--below N]"] };
      }
      const code = normalizeCode(args[0]);
      const codeErr = validateCode(code);
      if (codeErr) return { action: "add", errors: [codeErr] };

      const data: Record<string, unknown> = {};
      if (flags.env?.toLowerCase() === "prod" || flags.cost || flags.shares) {
        data.type = "holding";
      }
      if (flags.cost) {
        const cost = Number(flags.cost);
        if (isNaN(cost) || cost <= 0) return { action: "add", errors: ["--cost must be a positive number"] };
        data.cost = cost;
      }
      if (flags.shares) {
        const shares = Number(flags.shares);
        if (isNaN(shares) || shares <= 0) return { action: "add", errors: ["--shares must be a positive number"] };
        data.shares = shares;
      }
      if (flags.above) {
        const above = Number(flags.above);
        if (isNaN(above)) return { action: "add", errors: ["--above must be a number"] };
        data.above = above;
      }
      if (flags.below) {
        const below = Number(flags.below);
        if (isNaN(below)) return { action: "add", errors: ["--below must be a number"] };
        data.below = below;
      }

      // holding requires cost and shares
      if (data.type === "holding" && (!data.cost || !data.shares)) {
        if (flags.env?.toLowerCase() === "prod" && !data.cost && !data.shares) {
          return { action: "add", errors: ["Holding requires --cost and --shares"] };
        }
      }

      return { action: "add", code, data };
    }

    case "update": {
      if (args.length === 0) {
        return { action: "update", errors: ["Usage: svc update <code> [--cost N] [--shares N] [--above N] [--below N] [--env prod|dev]"] };
      }
      const code = normalizeCode(args[0]);
      const codeErr = validateCode(code);
      if (codeErr) return { action: "update", errors: [codeErr] };

      const data: Record<string, unknown> = {};
      if (flags.env) {
        data.type = flags.env.toLowerCase() === "prod" ? "holding" : "watching";
      }
      if (flags.cost) {
        const cost = Number(flags.cost);
        if (isNaN(cost) || cost <= 0) return { action: "update", errors: ["--cost must be a positive number"] };
        data.cost = cost;
      }
      if (flags.shares) {
        const shares = Number(flags.shares);
        if (isNaN(shares) || shares <= 0) return { action: "update", errors: ["--shares must be a positive number"] };
        data.shares = shares;
      }
      if (flags.above) {
        const above = Number(flags.above);
        if (isNaN(above)) return { action: "update", errors: ["--above must be a number"] };
        data.above = above;
      }
      if (flags.below) {
        const below = Number(flags.below);
        if (isNaN(below)) return { action: "update", errors: ["--below must be a number"] };
        data.below = below;
      }

      if (Object.keys(data).length === 0) {
        return { action: "update", errors: ["Nothing to update. Provide at least one flag."] };
      }

      return { action: "update", code, data };
    }

    case "rm":
    case "remove":
    case "del":
    case "delete": {
      if (args.length === 0) {
        return { action: "remove", errors: ["Usage: svc rm <code> [code2 ...]"] };
      }
      const codes: string[] = [];
      const errors: string[] = [];
      for (const a of args) {
        const c = normalizeCode(a);
        const err = validateCode(c);
        if (err) errors.push(err);
        else codes.push(c);
      }
      if (errors.length > 0) return { action: "remove", errors };
      return { action: "remove", codes };
    }

    case "ls":
    case "list": {
      const filter = args[0]?.toLowerCase();
      const data: Record<string, unknown> = {};
      if (filter === "prod" || filter === "holding") data.filter = "holding";
      else if (filter === "dev" || filter === "watching") data.filter = "watching";
      return { action: "list", data };
    }

    case "config":
    case "set": {
      if (args.length < 2) {
        return { action: "config", errors: ["Usage: svc config <key> <value>"] };
      }
      const key = args[0];
      const val = Number(args[1]);
      if (isNaN(val)) {
        return { action: "config", errors: [`Value must be a number: ${args[1]}`] };
      }
      return { action: "config", settings: { [key]: val } };
    }

    case "help":
    case "h":
    case "?": {
      return { action: "help" };
    }

    default:
      return { action: "help", errors: [`Unknown command: ${sub}`] };
  }
}

export const SUBCOMMANDS = ["add", "update", "rm", "ls", "config", "help"];

export function getCompletions(partial: string): string[] {
  const trimmed = partial.trim();
  const tokens = tokenize(trimmed);

  // complete "svc" prefix
  if (tokens.length === 0 || (tokens.length === 1 && !trimmed.endsWith(" "))) {
    if (!trimmed || "svc".startsWith(trimmed.toLowerCase())) return ["svc "];
    return [];
  }

  // after "svc", complete subcommand
  const hasSvc = tokens[0]?.toLowerCase() === "svc";
  const subIdx = hasSvc ? 1 : 0;

  if (tokens.length <= subIdx + 1 && !trimmed.endsWith(" ")) {
    const sub = tokens[subIdx]?.toLowerCase() || "";
    return SUBCOMMANDS.filter((s) => s.startsWith(sub)).map(
      (s) => (hasSvc ? "svc " : "") + s + " "
    );
  }

  return [];
}
