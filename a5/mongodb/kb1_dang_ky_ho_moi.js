// KB1.2 Thu thập dữ liệu dân cư: đăng ký 1 hộ mới với các nhân khẩu.
// Tương ứng a5/mysql/kb1_dang_ky_ho_moi.sql. Chạy trong mongosh (hoặc tab mongosh của Compass):
//   load("C:/GitHub/N21-INT3202-2/a5/mongodb/kb1_dang_ky_ho_moi.js")
// Cả hộ + nhân khẩu là 1 document nên chỉ cần 1 lệnh insertOne (nguyên tử, không cần JOIN).
// Ghi thêm biendong_cutru là document thứ 2, nên cần transaction (chỉ có trên replica set).
db = db.getSiblingDB("ipums_vn2019");

const serial = 900000000001;
// Xóa dữ liệu thử của lần chạy trước để script chạy lại được.
db.hokhau.deleteMany({ _id: { $gte: 900000000000 } });
db.biendong_cutru.deleteMany({ SERIAL: { $gte: 900000000000 } });
const mau = db.hokhau.findOne({ _id: 6000 }, { nhankhau: 0 });  // thông tin hộ lấy theo hộ 6000

const nk = (pernum, relate, related, age, sex, marst, marstd, agemarr, marryr, birthyr, school,
            lit, edattain, edattaind, yrschool, educvn, empstat, empstatd, labforce, classwk,
            classwkd) => ({
  PERNUM: pernum, PERWT: 30.0, RELATE: relate, RELATED: related, AGE: age, SEX: sex,
  MARST: marst, MARSTD: marstd, AGEMARR: agemarr, MARRYR: marryr, BIRTHYR: birthyr, BTHCERT: 9,
  RELIGION: 1, RELIGIOND: 1000, ETHNICVN: 1, SCHOOL: school, LIT: lit, EDATTAIN: edattain,
  EDATTAIND: edattaind, YRSCHOOL: yrschool, EDUCVN: educvn, EMPSTAT: empstat,
  EMPSTATD: empstatd, LABFORCE: labforce, CLASSWK: classwk, CLASSWKD: classwkd, WRKAVAIL: 0,
  MIGRATE5: 11, GEOMIG1_5: 704001, MIGCAUSE: 0,
});

const hoMoi = {
  ...mau, _id: serial, SERIAL: serial, PERSONS: 3,
  nhankhau: [
    nk(1, 1, 1000, 35, 1, 2, 200, 27, 2011, 1984, 3, 1, 3, 321, 12, 242, 1, 110, 1, 2, 200),
    nk(2, 2, 2000, 32, 2, 2, 200, 24, 2011, 1987, 3, 1, 3, 321, 12, 242, 1, 110, 1, 2, 200),
    nk(3, 3, 3000, 6, 1, 1, 100, 0, 9999, 2013, 2, 1, 1, 110, 1, 201, 0, 0, 9, 0, 0),
  ],
};

const coTransaction = !!(db.hello().setName || db.hello().msg === "isdbgrid");
if (coTransaction) {
  const session = db.getMongo().startSession();
  const sdb = session.getDatabase("ipums_vn2019");
  session.withTransaction(() => {
    sdb.hokhau.insertOne(hoMoi);
    sdb.biendong_cutru.insertOne({ SERIAL: serial, PERNUM: null, LOAI: "DANG_KY_HO_MOI",
                                   SERIAL_LIEN_QUAN: null, NGAY: new Date() });
  });
  session.endSession();
} else {
  // Standalone mongod: không có transaction nhiều document.
  db.hokhau.insertOne(hoMoi);
  db.biendong_cutru.insertOne({ SERIAL: serial, PERNUM: null, LOAI: "DANG_KY_HO_MOI",
                                SERIAL_LIEN_QUAN: null, NGAY: new Date() });
}

printjson(db.hokhau.findOne({ _id: serial }, { GEO1_VN: 1, PERSONS: 1, nhankhau: 1 }));

// DỌN DẸP
// db.hokhau.deleteMany({ _id: { $gte: 900000000000 } });
// db.biendong_cutru.deleteMany({ SERIAL: { $gte: 900000000000 } });
