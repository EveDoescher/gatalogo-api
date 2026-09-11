import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [workbookPath, resultsPath] = process.argv.slice(2);
if (!workbookPath || !resultsPath) throw new Error("Informe planilha e resultados.");

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const data = JSON.parse(await fs.readFile(resultsPath, "utf8"));
const review = workbook.worksheets.getItem("Revisao");
const summary = workbook.worksheets.getItem("Resumo");
const rows = review.getRange("A6:M35").values;
const resultsById = new Map(data.results.map((item) => [item.reference.sample_id, item]));

review.getRange("N5:V5").values = [[
  "Status API",
  "Cor API",
  "Cores API",
  "Padrão API",
  "Confiança",
  "Cor confere?",
  "Cores conferem?",
  "Padrão confere?",
  "Prévia da máscara",
]];
const comparisonRows = rows.map((row) => {
  const result = resultsById.get(row[0]);
  if (!result) return ["Não processada", "", "", "", null, "", "", "", ""];
  if (result.status === "error") return [`Erro: ${result.error}`, "", "", "", null, "", "", "", ""];
  const api = result.api;
  return [
    "OK",
    api.primary_color,
    api.colors.map((color) => `${color.name} ${color.percentage.toFixed(1)}%`).join("; "),
    api.coat_type,
    api.confidence,
    result.comparison.primary_match ? "OK" : "DIVERGE",
    result.comparison.colors_match ? "OK" : "DIVERGE",
    result.comparison.pattern_match ? "OK" : "DIVERGE",
    result.mask_preview ?? "",
  ];
});
review.getRange("N6:V35").values = comparisonRows;
review.getRange("N5:V5").format = {
  fill: "#0F766E",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
};
review.getRange("N6:V35").format = {
  borders: { preset: "inside", style: "thin", color: "#D9E2EC" },
  verticalAlignment: "center",
  wrapText: true,
};
review.getRange("N6:V35").format.rowHeight = 30;
review.getRange("R6:R35").format.numberFormat = "0%";
review.getRange("N6:N35").conditionalFormats.add("containsText", { text: "OK", format: { fill: "#BBF7D0", font: { color: "#166534" } } });
review.getRange("N6:N35").conditionalFormats.add("containsText", { text: "Erro", format: { fill: "#FECACA", font: { color: "#991B1B" } } });
for (const range of ["S6:U35"]) {
  review.getRange(range).conditionalFormats.add("containsText", { text: "DIVERGE", format: { fill: "#FDE68A", font: { color: "#92400E" } } });
}
const comparisonWidths = [19, 16, 34, 16, 13, 16, 18, 17, 48];
for (let index = 0; index < comparisonWidths.length; index += 1) {
  review.getRangeByIndexes(0, 13 + index, 1, 1).format.columnWidth = comparisonWidths[index];
}
review.freezePanes.freezeRows(5);

summary.getRange("A17:H17").merge();
summary.getRange("A17").values = [["Comparação automática com as referências humanas"]];
summary.getRange("A17:H17").format = { fill: "#0F766E", font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center" };
summary.getRange("A18:H18").values = [["Processadas", "", "", "Cor principal", "", "Cores visíveis", "", "Padrão"]];
summary.getRange("A19:H19").formulas = [[
  "=COUNTIF('Revisao'!$N$6:$N$35,\"OK\")", null, null,
  "=COUNTIF('Revisao'!$S$6:$S$35,\"OK\")", null,
  "=COUNTIF('Revisao'!$T$6:$T$35,\"OK\")", null,
  "=COUNTIF('Revisao'!$U$6:$U$35,\"OK\")",
]];
summary.getRange("A18:H19").format = { horizontalAlignment: "center", borders: { preset: "all", style: "thin", color: "#D9E2EC" } };
summary.getRange("A19:H19").format = { fill: "#E6FFFB", font: { bold: true, color: "#17324D", size: 14 } };
summary.mergeCells("A22:H23");
summary.getRange("A22").values = [["A comparação de cores usa conjunto exato: uma cor extra ou ausente conta como divergência. Revise visualmente as prévias da máscara antes de ajustar os limiares do detector."]];
summary.getRange("A22:H23").format = { fill: "#EEF6FF", font: { color: "#17324D", italic: true }, wrapText: true, verticalAlignment: "center" };

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(workbookPath);
const preview = await workbook.render({ sheetName: "Resumo", range: "A1:H23", scale: 1.5, format: "png" });
await fs.writeFile(path.join(path.dirname(workbookPath), "resumo_comparacao_preview.png"), new Uint8Array(await preview.arrayBuffer()));
console.log(JSON.stringify(data.summary));
