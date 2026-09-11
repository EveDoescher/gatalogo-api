import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workbookPath = process.argv[2];
if (!workbookPath) throw new Error("Informe o caminho da planilha.");

const input = await FileBlob.load(workbookPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const review = workbook.worksheets.getItem("Revisao");
const rows = review.getRange("A6:M35").values;
const requiredColumns = [5, 6, 7, 8, 9];
let completed = 0;
for (let index = 0; index < rows.length; index += 1) {
  const row = rows[index];
  if (row[0] && requiredColumns.every((column) => row[column])) {
    review.getRangeByIndexes(index + 5, 11, 1, 1).values = [["Concluído"]];
    completed += 1;
  }
}
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(workbookPath);
console.log(JSON.stringify({ completed }));
