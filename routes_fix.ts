/**
 * routes.ts  –  corrected /api/process handler
 *
 * Key fixes:
 *  1. Spawn process_drawing.py (not the old scripts directly)
 *  2. Collect stdout lines separately from stderr lines
 *  3. Parse only the LAST non-empty stdout line as JSON
 *  4. Stream stderr to server console for debugging
 */

import { spawn }       from "child_process";
import * as path       from "path";
import * as fs         from "fs";
import * as os         from "os";
import express         from "express";
import multer          from "multer";

const router = express.Router();
const upload = multer({ dest: os.tmpdir() });

const PYTHON_SCRIPT = path.join(__dirname, "../../gfrc_tool/process_drawing.py");
const PYTHON_BIN    = process.env.PYTHON_BIN ?? "python3";

// ─────────────────────────────────────────────────────────────────────────────
//  POST /api/process
// ─────────────────────────────────────────────────────────────────────────────
router.post("/api/process", upload.single("file"), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ message: "No file uploaded" });
  }

  // Options forwarded from the frontend (unit costs, tolerances, etc.)
  let opts: Record<string, unknown> = {};
  if (req.body.options) {
    try {
      opts = JSON.parse(req.body.options);
    } catch {
      // ignore, use defaults
    }
  }

  const filePath   = req.file.path;
  const optsString = JSON.stringify(opts);

  return new Promise<void>((resolve) => {
    const stdoutLines: string[] = [];
    const stderrLines: string[] = [];

    const child = spawn(PYTHON_BIN, [PYTHON_SCRIPT, filePath, optsString], {
      env: { ...process.env },
    });

    // ── Collect stdout (JSON only) ──────────────────────────────────────────
    child.stdout.on("data", (chunk: Buffer) => {
      stdoutLines.push(chunk.toString());
    });

    // ── Pipe stderr to server console (progress messages, warnings) ─────────
    child.stderr.on("data", (chunk: Buffer) => {
      const text = chunk.toString().trim();
      stderrLines.push(text);
      console.log("[python]", text);          // visible in Replit console
    });

    child.on("close", (code) => {
      // Clean up the temp upload file
      try { fs.unlinkSync(filePath); } catch { /* ignore */ }

      // Join all stdout and find the last non-empty line
      const allStdout = stdoutLines.join("").trim();
      const lines     = allStdout.split("\n").map(l => l.trim()).filter(Boolean);
      const jsonLine  = lines[lines.length - 1] ?? "";

      if (!jsonLine) {
        const errSummary = stderrLines.slice(-5).join(" | ");
        console.error("Processing error: no JSON output from Python");
        res.status(500).json({
          message: "Python script produced no output",
          stderr:  errSummary,
        });
        resolve();
        return;
      }

      let payload: unknown;
      try {
        payload = JSON.parse(jsonLine);              // ← safe: only the JSON line
      } catch (e) {
        console.error("Processing error: JSON.parse failed:", (e as Error).message);
        console.error("Raw stdout was:", jsonLine.slice(0, 200));
        res.status(500).json({
          message: `Could not parse Python output: ${(e as Error).message}`,
          raw:     jsonLine.slice(0, 500),
        });
        resolve();
        return;
      }

      // Surface any Python-level errors as 500
      if (
        typeof payload === "object" &&
        payload !== null &&
        (payload as Record<string,unknown>).ok === false
      ) {
        res.status(500).json({
          message: (payload as Record<string,unknown>).error ?? "Unknown Python error",
        });
        resolve();
        return;
      }

      res.json(payload);
      resolve();
    });

    child.on("error", (err) => {
      try { fs.unlinkSync(filePath); } catch { /* ignore */ }
      console.error("spawn error:", err);
      res.status(500).json({ message: err.message });
      resolve();
    });
  });
});

export default router;
