import { NextResponse } from "next/server";
import { readFileSync } from "fs";
import { join } from "path";

const SUMMARY_PATH = join(process.cwd(), "..", "src", "data", "daily_summary.json");

/**
 * GET /api/summary
 *
 * Reads daily_summary.json written by the Python daily_summary_generator.
 * Returns { data: null } when no summary file exists.
 */
export async function GET() {
  try {
    const raw = readFileSync(SUMMARY_PATH, "utf-8");
    const data = JSON.parse(raw);
    return NextResponse.json({ data });
  } catch {
    return NextResponse.json({ data: null });
  }
}
