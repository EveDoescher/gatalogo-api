import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workbookPath = process.argv[2];
if (!workbookPath) throw new Error("Informe o caminho da planilha.");

const input = await FileBlob.load(workbookPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const review = workbook.worksheets.getItem("Revisão");
review.name = "Revisao";
const summary = workbook.worksheets.getItem("Resumo");

summary.getRange("A5").formulas = [["=ROWS('Revisao'!$A$6:$A$35)"]];
summary.getRange("D5").formulas = [["=COUNTIF('Revisao'!$D$6:$D$35,\"calibration\")"]];
summary.getRange("G5").formulas = [["=COUNTIF('Revisao'!$D$6:$D$35,\"evaluation\")"]];
summary.getRange("A10").formulas = [["=ROWS('Revisao'!$A$6:$A$35)"]];
summary.getRange("D10").formulas = [["=COUNTIF('Revisao'!$L$6:$L$35,\"Pendente\")"]];
summary.getRange("G10").formulas = [["=COUNTIF('Revisao'!$L$6:$L$35,\"Concluído\")"]];

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(workbookPath);
const preview = await workbook.render({ sheetName: "Resumo", range: "A1:H14", scale: 1.5, format: "png" });
await fs.writeFile(
  path.join(path.dirname(workbookPath), "resumo_preview_corrigido.png"),
  new Uint8Array(await preview.arrayBuffer()),
);
const check = await workbook.inspect({
  kind: "table",
  range: "Resumo!A1:H14",
  include: "values,formulas",
  tableMaxRows: 14,
  tableMaxCols: 8,
});
console.log(check.ndjson);
