import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const projectRoot = process.argv[2];
const outputDir = process.argv[3];
if (!projectRoot || !outputDir) {
  throw new Error("Uso: node build_own_cats_review_workbook.mjs <projectRoot> <outputDir>");
}

const manifestPath = path.join(projectRoot, "data", "own-cats", "own_cats_v1.csv");
const csv = await fs.readFile(manifestPath, "utf8");
const [headerLine, ...dataLines] = csv.trim().split(/\r?\n/);
const headers = headerLine.split(",");
const manifestRows = dataLines.map((line) => {
  const values = line.split(",");
  return Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""]));
});

const workbook = Workbook.create();
const review = workbook.worksheets.add("Revisão");
const summary = workbook.worksheets.add("Resumo");
const lists = workbook.worksheets.add("Listas");

const navy = "#17324D";
const teal = "#0F766E";
const paleTeal = "#E6FFFB";
const paleYellow = "#FFF7D6";
const paleBlue = "#EEF6FF";
const border = "#D9E2EC";

review.showGridLines = false;
review.mergeCells("A1:M1");
review.getRange("A1").values = [["Revisão humana — fotos autorais de gatos"]];
review.getRange("A1:M1").format = {
  fill: navy,
  font: { bold: true, color: "#FFFFFF", size: 16 },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
review.getRange("A1:M1").format.rowHeight = 30;
review.mergeCells("A2:M2");
review.getRange("A2").values = [[
  "Preencha as colunas em amarelo após revisar a foto. Mantenha fotos do mesmo grupo de captura no mesmo split.",
]];
review.getRange("A2:M2").format = {
  fill: paleBlue,
  font: { color: navy, italic: true },
  wrapText: true,
  verticalAlignment: "center",
};
review.getRange("A2:M2").format.rowHeight = 32;

const reviewHeaders = [[
  "ID da foto",
  "Arquivo",
  "Data da foto",
  "Split",
  "Grupo de captura",
  "Gato / apelido",
  "Filhote?",
  "Cor principal",
  "Outras cores visíveis",
  "Padrão",
  "Máscara correta?",
  "Status da revisão",
  "Observações",
]];
review.getRange("A5:M5").values = reviewHeaders;
review.getRange("A5:M5").format = {
  fill: teal,
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "outside", style: "thin", color: border },
};
review.getRange("A5:M5").format.rowHeight = 32;

const reviewRows = manifestRows.map((row) => [
  row.sample_id,
  row.image_path,
  row.capture_timestamp ? new Date(row.capture_timestamp) : null,
  row.split,
  row.similar_capture_group,
  row.subject_id,
  row.is_kitten,
  row.expected_primary_color,
  row.expected_colors,
  row.expected_coat_type,
  "",
  row.review_status,
  row.notes,
]);
review.getRange(`A6:M${5 + reviewRows.length}`).values = reviewRows;
review.getRange(`A6:M${5 + reviewRows.length}`).format = {
  borders: { preset: "inside", style: "thin", color: border },
  verticalAlignment: "center",
};
review.getRange(`C6:C${5 + reviewRows.length}`).format.numberFormat = "yyyy-mm-dd hh:mm";
review.getRange(`D6:E${5 + reviewRows.length}`).format.fill = "#F4F7FA";
review.getRange(`A6:E${5 + reviewRows.length}`).format.font = { color: "#334E68" };
review.getRange(`F6:M${5 + reviewRows.length}`).format.fill = paleYellow;
review.getRange(`I6:M${5 + reviewRows.length}`).format.wrapText = true;

const table = review.tables.add(`A5:M${5 + reviewRows.length}`, true, "OwnCatsReviewTable");
table.style = "TableStyleMedium2";
review.freezePanes.freezeRows(5);

const widths = [26, 47, 19, 15, 20, 21, 12, 16, 24, 16, 18, 19, 30];
for (let index = 0; index < widths.length; index += 1) {
  review.getRangeByIndexes(0, index, 1, 1).format.columnWidth = widths[index];
}
review.getRange(`A6:M${5 + reviewRows.length}`).format.rowHeight = 24;

lists.getRange("A1:D1").values = [["Filhote", "Cor", "Padrão", "Status"]];
lists.getRange("A2:D9").values = [
  ["Sim", "Preto", "Sólido", "Pendente"],
  ["Não", "Branco", "Bicolor", "Concluído"],
  ["Não sei", "Cinza", "Tricolor", "Excluir"],
  [null, "Laranja", "Rajado", null],
  [null, "Marrom", "Tuxedo", null],
  [null, "Creme", "Escaminha", null],
  [null, "Outro", "Colorpoint", null],
  [null, null, "Outro", null],
];
lists.getRange("A1:D1").format = { fill: navy, font: { bold: true, color: "#FFFFFF" } };
lists.getRange("A1:D9").format.borders = { preset: "inside", style: "thin", color: border };
lists.getRange("A:D").format.columnWidth = 18;
lists.showGridLines = false;

const lastRow = 5 + reviewRows.length;
review.getRange(`G6:G${lastRow}`).dataValidation = { rule: { type: "list", formula1: "'Listas'!$A$2:$A$4" } };
review.getRange(`H6:H${lastRow}`).dataValidation = { rule: { type: "list", formula1: "'Listas'!$B$2:$B$8" } };
review.getRange(`J6:J${lastRow}`).dataValidation = { rule: { type: "list", formula1: "'Listas'!$C$2:$C$9" } };
review.getRange(`K6:K${lastRow}`).dataValidation = { rule: { type: "list", values: ["Sim", "Não", "Não sei"] } };
review.getRange(`L6:L${lastRow}`).dataValidation = { rule: { type: "list", formula1: "'Listas'!$D$2:$D$4" } };
review.getRange(`L6:L${lastRow}`).conditionalFormats.add("containsText", { text: "Pendente", format: { fill: "#FDE68A", font: { color: "#92400E" } } });
review.getRange(`L6:L${lastRow}`).conditionalFormats.add("containsText", { text: "Concluído", format: { fill: "#BBF7D0", font: { color: "#166534" } } });
review.getRange(`L6:L${lastRow}`).conditionalFormats.add("containsText", { text: "Excluir", format: { fill: "#FECACA", font: { color: "#991B1B" } } });

summary.showGridLines = false;
summary.mergeCells("A1:H1");
summary.getRange("A1").values = [["Resumo — benchmark de fotos autorais"]];
summary.getRange("A1:H1").format = { fill: navy, font: { bold: true, color: "#FFFFFF", size: 16 }, horizontalAlignment: "center" };
summary.getRange("A1:H1").format.rowHeight = 30;
summary.getRange("A3:H3").values = [["Fotos", "", "", "Calibração", "", "", "Avaliação final", ""]];
summary.getRange("A3:H3").format = { fill: teal, font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center" };
summary.getRange("A4:H4").values = [["Total", null, null, "Quantidade", null, null, "Quantidade", null]];
summary.getRange("A5:H5").formulas = [[
  "=COUNTA('Revisão'!$A$6:$A$35)", null, null,
  "=COUNTIF('Revisão'!$D$6:$D$35,\"calibration\")", null, null,
  "=COUNTIF('Revisão'!$D$6:$D$35,\"evaluation\")", null,
]];
summary.getRange("A4:H5").format = { horizontalAlignment: "center", borders: { preset: "all", style: "thin", color: border } };
summary.getRange("A5:H5").format = { fill: paleTeal, font: { bold: true, color: navy, size: 14 } };
summary.getRange("A8:H8").values = [["Revisão", "", "", "Pendente", "", "", "Concluída", ""]];
summary.getRange("A8:H8").format = { fill: teal, font: { bold: true, color: "#FFFFFF" }, horizontalAlignment: "center" };
summary.getRange("A9:H9").values = [["Total", null, null, "Fotos", null, null, "Fotos", null]];
summary.getRange("A10:H10").formulas = [[
  "=COUNTA('Revisão'!$A$6:$A$35)", null, null,
  "=COUNTIF('Revisão'!$L$6:$L$35,\"Pendente\")", null, null,
  "=COUNTIF('Revisão'!$L$6:$L$35,\"Concluído\")", null,
]];
summary.getRange("A9:H10").format = { horizontalAlignment: "center", borders: { preset: "all", style: "thin", color: border } };
summary.getRange("A10:H10").format = { fill: paleYellow, font: { bold: true, color: navy, size: 14 } };
summary.mergeCells("A13:H14");
summary.getRange("A13").values = [["Use as 20 fotos de calibração apenas para ajustar limites e escolha de modelos. Mantenha as 10 fotos de avaliação final sem consulta durante os ajustes; elas formam a comparação imparcial."]];
summary.getRange("A13:H14").format = { fill: paleBlue, font: { color: navy, italic: true }, wrapText: true, verticalAlignment: "center", borders: { preset: "outside", style: "thin", color: border } };
for (const column of ["A", "B", "C", "D", "E", "F", "G", "H"]) summary.getRange(`${column}:${column}`).format.columnWidth = 16;

await fs.mkdir(outputDir, { recursive: true });
const reviewPreview = await workbook.render({ sheetName: "Revisão", range: "A1:M20", scale: 1.25, format: "png" });
await fs.writeFile(path.join(outputDir, "revisao_preview.png"), new Uint8Array(await reviewPreview.arrayBuffer()));
const summaryPreview = await workbook.render({ sheetName: "Resumo", range: "A1:H14", scale: 1.5, format: "png" });
await fs.writeFile(path.join(outputDir, "resumo_preview.png"), new Uint8Array(await summaryPreview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(path.join(outputDir, "revisao_pelagem_gatos.xlsx"));

const inspection = await workbook.inspect({
  kind: "table",
  range: "Revisão!A1:M10",
  include: "values,formulas",
  tableMaxRows: 10,
  tableMaxCols: 13,
});
console.log(inspection.ndjson);
