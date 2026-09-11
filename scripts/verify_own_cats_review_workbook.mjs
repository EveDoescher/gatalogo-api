import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workbookPath = process.argv[2];
if (!workbookPath) throw new Error("Informe o caminho da planilha.");

const input = await FileBlob.load(workbookPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const summary = await workbook.inspect({
  kind: "table",
  range: "Resumo!A1:H14",
  include: "values,formulas",
  tableMaxRows: 14,
  tableMaxCols: 8,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "verificação de erros de fórmula",
});
const review = workbook.worksheets.getItem("Revisao");
const rows = review.getRange("A6:M35").values;
const requiredColumns = [5, 6, 7, 8, 9];
const incomplete = rows
  .filter((row) => row[0])
  .filter((row) => requiredColumns.some((index) => !row[index]))
  .map((row) => row[0]);
console.log(summary.ndjson);
console.log(errors.ndjson);
console.log(JSON.stringify({
  reviewedRows: rows.filter((row) => row[0]).length,
  incompleteReferenceRows: incomplete,
}));
