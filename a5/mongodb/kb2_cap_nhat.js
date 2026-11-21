// KB2 Cập nhật dữ liệu dân cư. Tương ứng a5/mysql/kb2_cap_nhat.sql.
//   load("C:/GitHub/N21-INT3202-2/a5/mongodb/kb2_cap_nhat.js")
db = db.getSiblingDB("ipums_vn2019");

// ---------------------------------------------------------------------------
// KB2.1 Cập nhật 1 nhân khẩu: đăng ký kết hôn.
// Nhân khẩu nằm trong mảng nhankhau, nên dùng arrayFilters để chọn đúng PERNUM.
// ---------------------------------------------------------------------------
const serial = 6000, pernum = 2;
const cu = db.hokhau.findOne({ _id: serial }).nhankhau.find(p => p.PERNUM === pernum);

db.hokhau.updateOne(
  { _id: serial },
  { $set: { "nhankhau.$[p].MARST": 2, "nhankhau.$[p].MARSTD": 200,
            "nhankhau.$[p].AGEMARR": cu.AGE, "nhankhau.$[p].MARRYR": 2019 } },
  { arrayFilters: [{ "p.PERNUM": pernum }] }
);
printjson(db.hokhau.findOne({ _id: serial }).nhankhau.find(p => p.PERNUM === pernum));

// Trả lại giá trị cũ
db.hokhau.updateOne(
  { _id: serial },
  { $set: { "nhankhau.$[p].MARST": cu.MARST, "nhankhau.$[p].MARSTD": cu.MARSTD,
            "nhankhau.$[p].AGEMARR": cu.AGEMARR, "nhankhau.$[p].MARRYR": cu.MARRYR } },
  { arrayFilters: [{ "p.PERNUM": pernum }] }
);

// ---------------------------------------------------------------------------
// KB2.2 Cập nhật hàng loạt: tăng tuổi cho toàn bộ dân 1 tỉnh.
// GEO1_VN và AGE cùng nằm trong 1 document: không cần JOIN; $[] = mọi phần tử của mảng.
// ---------------------------------------------------------------------------
const province = 704001;
printjson(db.hokhau.updateMany({ GEO1_VN: province }, { $inc: { "nhankhau.$[].AGE": 1 } }));
// Trả lại như cũ
db.hokhau.updateMany({ GEO1_VN: province }, { $inc: { "nhankhau.$[].AGE": -1 } });
