import { join } from "path";

export const SIM_DB_PATH = join(
  process.cwd(),
  "..",
  "src",
  "data",
  "sim_trading.db"
);
