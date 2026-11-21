// KB3 Đăng ký / quản lý cư trú. Tương ứng a5/mysql/kb3_cu_tru.sql.
//   load("C:/GitHub/N21-INT3202-2/a5/mongodb/kb3_cu_tru.js")
// Chạy kb1_dang_ky_ho_moi.js trước để có hộ thử nghiệm A.
db = db.getSiblingDB("ipums_vn2019");

const A = 900000000001, B = 900000000002, C = 900000000003;
const coTransaction = !!(db.hello().setName || db.hello().msg === "isdbgrid");

// Chạy fn(sdb) trong transaction nếu là replica set, ngược lại chạy thẳng.
function giaoDich(fn) {
  if (!coTransaction) return fn(db);
  const session = db.getMongo().startSession();
  try {
    let kq;
    session.withTransaction(() => { kq = fn(session.getDatabase("ipums_vn2019")); });
    return kq;
  } finally {
    session.endSession();
  }
}
const ghiBienDong = (d, serial, pernum, loai, lienQuan = null) =>
  d.biendong_cutru.insertOne({ SERIAL: serial, PERNUM: pernum, LOAI: loai,
                               SERIAL_LIEN_QUAN: lienQuan, NGAY: new Date() });

// Xóa B, C của lần chạy trước (hộ A do kb1 tạo lại).
db.hokhau.deleteMany({ _id: { $in: [B, C] } });

// Hộ B: bản sao 1 hộ thật 2 người, ưu tiên ở tỉnh khác hộ A, để làm nơi chuyển đến.
const tinhA = db.hokhau.findOne({ _id: A }).GEO1_VN;
const mau = db.hokhau.find({ PERSONS: 2, _id: { $lt: 900000000000 }, GEO1_VN: { $ne: tinhA } })
              .sort({ _id: 1 }).limit(1).tryNext()
         ?? db.hokhau.find({ PERSONS: 2, _id: { $lt: 900000000000 } }).sort({ _id: 1 }).limit(1).next();
db.hokhau.insertOne({ ...mau, _id: B, SERIAL: B });

// ---------------------------------------------------------------------------
// KB3.1 Khai sinh: 1 lệnh cập nhật nguyên tử trên 1 document (pipeline update),
// tự tính PERNUM = max + 1 và tăng PERSONS - không cần khóa như SELECT ... FOR UPDATE.
// ---------------------------------------------------------------------------
giaoDich(d => {
  const treSoSinh = { PERWT: 30.0, RELATE: 3, RELATED: 3000, AGE: 0, SEX: 1, MARST: 1,
    MARSTD: 100, AGEMARR: 0, MARRYR: 9999, BIRTHYR: 2019, BTHCERT: 9, RELIGION: 1,
    RELIGIOND: 1000, ETHNICVN: 1, SCHOOL: 0, LIT: 0, EDATTAIN: 0, EDATTAIND: 0, YRSCHOOL: 0,
    EDUCVN: 0, EMPSTAT: 0, EMPSTATD: 0, LABFORCE: 9, CLASSWK: 0, CLASSWKD: 0, WRKAVAIL: 0,
    MIGRATE5: 0, GEOMIG1_5: 0, MIGCAUSE: 0 };
  const ho = d.hokhau.findOneAndUpdate(
    { _id: A },
    [{ $set: {
      nhankhau: { $concatArrays: ["$nhankhau", [{ $mergeObjects: [
        { $literal: treSoSinh },
        { PERNUM: { $add: [{ $ifNull: [{ $max: "$nhankhau.PERNUM" }, 0] }, 1] } },
      ] }]] },
      PERSONS: { $add: ["$PERSONS", 1] },
    } }],
    { projection: { nhankhau: { $slice: -1 } }, returnDocument: "after" }
  );
  ghiBienDong(d, A, ho.nhankhau[0].PERNUM, "KHAI_SINH");
});

// ---------------------------------------------------------------------------
// KB3.2 Chuyển hộ khẩu: nhân khẩu cuối của hộ A sang hộ B. Sửa 2 document -> cần transaction.
// ---------------------------------------------------------------------------
giaoDich(d => {
  const nguoi = d.hokhau.findOne({ _id: A }).nhankhau.reduce((x, y) => (x.PERNUM > y.PERNUM ? x : y));
  const pDen = Math.max(0, ...d.hokhau.findOne({ _id: B }).nhankhau.map(p => p.PERNUM)) + 1;
  d.hokhau.updateOne({ _id: A }, { $pull: { nhankhau: { PERNUM: nguoi.PERNUM } },
                                   $inc: { PERSONS: -1 } });
  d.hokhau.updateOne({ _id: B }, { $push: { nhankhau: { ...nguoi, PERNUM: pDen } },
                                   $inc: { PERSONS: 1 } });
  ghiBienDong(d, A, nguoi.PERNUM, "CHUYEN_DI", B);
  ghiBienDong(d, B, pDen, "CHUYEN_DEN", A);
});

// ---------------------------------------------------------------------------
// KB3.3 Tách hộ: thành viên PERNUM >= 2 của hộ A lập hộ mới C.
// ---------------------------------------------------------------------------
giaoDich(d => {
  const ho = d.hokhau.findOne({ _id: A });
  const tach = ho.nhankhau.filter(p => p.PERNUM >= 2)
                          .map((p, i) => ({ ...p, PERNUM: i + 1 }));
  Object.assign(tach[0], { RELATE: 1, RELATED: 1000 });          // chủ hộ mới
  const { nhankhau, ...thongTinHo } = ho;
  d.hokhau.insertOne({ ...thongTinHo, _id: C, SERIAL: C, PERSONS: tach.length, nhankhau: tach });
  d.hokhau.updateOne({ _id: A }, { $pull: { nhankhau: { PERNUM: { $gte: 2 } } },
                                   $inc: { PERSONS: -tach.length } });
  ghiBienDong(d, C, null, "TACH_HO", A);
});

// ---------------------------------------------------------------------------
// KB3.4 Xóa đăng ký thường trú: nhân khẩu có PERNUM lớn nhất của hộ C, 1 lệnh nguyên tử.
// ---------------------------------------------------------------------------
giaoDich(d => {
  const truoc = d.hokhau.findOneAndUpdate(
    { _id: C },
    [{ $set: {
      nhankhau: { $filter: { input: "$nhankhau",
                             cond: { $ne: ["$$this.PERNUM", { $max: "$nhankhau.PERNUM" }] } } },
      PERSONS: { $subtract: ["$PERSONS", 1] },
    } }],
    { projection: { "nhankhau.PERNUM": 1 } }   // trả về document TRƯỚC khi sửa
  );
  ghiBienDong(d, C, Math.max(...truoc.nhankhau.map(p => p.PERNUM)), "XOA_DANG_KY");
});

// Kết quả
db.hokhau.find({ _id: { $gte: 900000000000 } }, { GEO1_VN: 1, PERSONS: 1, "nhankhau.PERNUM": 1,
                                                  "nhankhau.AGE": 1, "nhankhau.RELATE": 1 })
  .forEach(printjson);
db.biendong_cutru.find({ SERIAL: { $gte: 900000000000 } }).sort({ _id: 1 }).forEach(printjson);

// DỌN DẸP
// db.hokhau.deleteMany({ _id: { $gte: 900000000000 } });
// db.biendong_cutru.deleteMany({ SERIAL: { $gte: 900000000000 } });
