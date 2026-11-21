// KB4 Tra cứu & thống kê: bản MongoDB của q1, q2, q3, q5, q6 trong a5/mysql/.
//   load("C:/GitHub/N21-INT3202-2/a5/mongodb/kb4_tra_cuu.js")
// Mỗi truy vấn in số kết quả và thời gian thực thi (executionTimeMillis) từ explain.
db = db.getSiblingDB("ipums_vn2019");

const province = 704001;

const truyVan = {
  // q1 / KB4.1: 1 hộ + nhân khẩu. Chỉ 1 document, không JOIN.
  "KB4.1 tra cuu ho": [{ $match: { _id: 6000 } }],

  // q2 / KB4.2: số người có việc làm theo tuổi.
  "KB4.2 viec lam theo tuoi": [
    { $unwind: "$nhankhau" },
    { $match: { "nhankhau.EMPSTAT": 1 } },
    { $group: { _id: "$nhankhau.AGE", n: { $sum: 1 } } },
    { $sort: { _id: 1 } },
  ],

  // q3 / KB4.3: người 18-60 tuổi của 1 tỉnh (mỗi dòng = 1 người, như kết quả JOIN của MySQL).
  "KB4.3 do tuoi lao dong": [
    { $match: { GEO1_VN: province, "nhankhau.AGE": { $gte: 18, $lte: 60 } } },
    { $unwind: "$nhankhau" },
    { $match: { "nhankhau.AGE": { $gte: 18, $lte: 60 } } },
    { $replaceRoot: { newRoot: { $mergeObjects: [
      "$nhankhau", { SERIAL: "$SERIAL", GEO1_VN: "$GEO1_VN" }] } } },
  ],

  // q5 / KB4.4: người chuyển đến tỉnh trong 5 năm, theo tỉnh nơi đi.
  "KB4.4 nhap cu": [
    { $match: { GEO1_VN: province, "nhankhau.MIGRATE5": 20 } },
    { $unwind: "$nhankhau" },
    { $match: { "nhankhau.MIGRATE5": 20 } },
    { $group: { _id: "$nhankhau.GEOMIG1_5", so_nguoi: { $sum: 1 } } },
    { $sort: { so_nguoi: -1, _id: 1 } },
  ],

  // q6 / KB4.5: tháp dân số tỉnh x giới tính x nhóm 5 tuổi.
  "KB4.5 thap dan so": [
    { $unwind: "$nhankhau" },
    { $group: {
      _id: { tinh: "$GEO1_VN", gioi: "$nhankhau.SEX",
             nhom: { $min: [{ $multiply: [{ $floor: { $divide: ["$nhankhau.AGE", 5] } }, 5] }, 80] } },
      n: { $sum: 1 },
    } },
    { $sort: { "_id.tinh": 1, "_id.gioi": 1, "_id.nhom": 1 } },
  ],
};

for (const [ten, pipeline] of Object.entries(truyVan)) {
  const kq = db.hokhau.aggregate(pipeline, { allowDiskUse: true }).toArray();
  const plan = db.hokhau.explain("executionStats").aggregate(pipeline, { allowDiskUse: true });
  const ms = plan.executionStats?.executionTimeMillis
          ?? plan.stages?.[0]?.$cursor?.executionStats?.executionTimeMillis;
  print(`${ten}: ${kq.length} kết quả, ${ms} ms`);
}

// Ví dụ xem 5 tỉnh nơi đi nhiều nhất của KB4.4:
printjson(db.hokhau.aggregate(truyVan["KB4.4 nhap cu"]).toArray().slice(0, 5));
