import fs from "node:fs/promises";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [workbookPath, outputPath] = process.argv.slice(2);
if (!workbookPath || !outputPath) {
  throw new Error("Uso: node extract_own_cats_references.mjs <planilha> <saida.json>");
}

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const review = workbook.worksheets.getItem("Revisao");
const rows = review.getRange("A6:M35").values;
const references = rows
  .filter((row) => row[0] && row[3] === "calibration" && row[11] === "Concluído")
  .map((row) => ({
    sample_id: row[0],
    image_path: row[1],
    expected_primary_color: row[7],
    expected_colors: row[8],
    expected_coat_type: row[9],
    is_kitten: row[6],
  }));

await fs.writeFile(outputPath, JSON.stringify(references, null, 2), "utf8");
console.log(JSON.stringify({ references: references.length }));
