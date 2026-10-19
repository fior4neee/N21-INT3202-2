import csv
import os
from pathlib import Path

# chia file ipumsi thành data hokhau và nhankhau

DATA_DIR = Path(__file__).resolve().parent / "data"
SOURCE = DATA_DIR / "ipumsi_00001.csv"
HOUSEHOLD_OUTPUT = DATA_DIR / "hokhau.csv"
PERSON_OUTPUT = DATA_DIR / "nhankhau.csv"

HOUSEHOLD_COLUMNS = (
    "COUNTRY", "YEAR", "SAMPLE", "SERIAL", "PERSONS", "SUBSAMP", "ENUMOP",
    "FORMTYPE", "ENUMTYPE", "ENUMMO", "ENUMHH", "SAMPMETH", "SAMPFRCT",
    "SAMPUNIT", "SAMPDRW", "URBAN", "REGIONW", "GEOLEV1", "GEOLEV2",
    "POPDENSGEO1", "POPDENSGEO2", "AREAMOLLWGEO1", "AREAMOLLWGEO2",
    "GEO1_VN", "GEO1_VN2019", "GEO2_VN", "GEO2_VN2019", "REGNVN",
    "OWNERSHIP", "OWNERSHIPD",
)
PERSON_COLUMNS = (
    "PERNUM", "PERWT", "RELATE", "RELATED", "AGE", "SEX", "MARST", "MARSTD",
    "AGEMARR", "MARRYR", "BIRTHYR", "BTHCERT", "RELIGION", "RELIGIOND",
    "ETHNICVN", "SCHOOL", "LIT", "EDATTAIN", "EDATTAIND", "YRSCHOOL",
    "EDUCVN", "EMPSTAT", "EMPSTATD", "LABFORCE", "CLASSWK", "CLASSWKD",
    "WRKAVAIL", "MIGRATE5", "GEOMIG1_5", "MIGCAUSE",
)


def temporary_path(path):
    return path.with_name(path.name + ".tmp")


def main():
    if not SOURCE.is_file():
        raise FileNotFoundError(f"Input CSV not found: {SOURCE}")

    household_temp = temporary_path(HOUSEHOLD_OUTPUT)
    person_temp = temporary_path(PERSON_OUTPUT)
    household_count = 0
    person_count = 0
    previous_serial = None
    previous_household = None
    persons_in_household = 0
    previous_pernum = 0

    try:
        with SOURCE.open("r", newline="", encoding="utf-8-sig") as source_file, \
                household_temp.open("w", newline="", encoding="utf-8") as household_file, \
                person_temp.open("w", newline="", encoding="utf-8") as person_file:
            reader = csv.DictReader(source_file)
            expected_columns = HOUSEHOLD_COLUMNS + PERSON_COLUMNS
            if tuple(reader.fieldnames or ()) != expected_columns:
                raise ValueError("Input CSV header does not match the expected IPUMS columns.")

            household_writer = csv.DictWriter(
                household_file, fieldnames=HOUSEHOLD_COLUMNS, lineterminator="\n"
            )
            person_writer = csv.DictWriter(
                person_file,
                fieldnames=("SERIAL",) + PERSON_COLUMNS,
                lineterminator="\n",
            )
            household_writer.writeheader()
            person_writer.writeheader()

            for line_number, row in enumerate(reader, start=2):
                if None in row or any(value is None or value == "" for value in row.values()):
                    raise ValueError(f"Malformed or blank field on input line {line_number}.")

                serial = int(row["SERIAL"])
                pernum = int(row["PERNUM"])
                declared_persons = int(row["PERSONS"])
                household_values = tuple(row[column] for column in HOUSEHOLD_COLUMNS)

                if serial != previous_serial:
                    if previous_serial is not None:
                        if persons_in_household != int(previous_household["PERSONS"]):
                            raise ValueError(
                                f"SERIAL {previous_serial} has {persons_in_household} "
                                f"rows but PERSONS is {previous_household['PERSONS']}."
                            )
                        if serial < previous_serial:
                            raise ValueError(
                                "Input rows are not grouped in ascending SERIAL order."
                            )

                    household_writer.writerow(
                        {column: row[column] for column in HOUSEHOLD_COLUMNS}
                    )
                    household_count += 1
                    previous_serial = serial
                    previous_household = row
                    previous_household_values = household_values
                    persons_in_household = 0
                    previous_pernum = 0
                elif household_values != previous_household_values:
                    raise ValueError(
                        f"Household fields differ between rows for SERIAL {serial}."
                    )

                if pernum <= previous_pernum or pernum > declared_persons:
                    raise ValueError(
                        f"Invalid PERNUM {pernum} for SERIAL {serial} "
                        f"on input line {line_number}."
                    )

                person_writer.writerow(
                    {"SERIAL": row["SERIAL"], **{column: row[column] for column in PERSON_COLUMNS}}
                )
                person_count += 1
                persons_in_household += 1
                previous_pernum = pernum

            if previous_serial is None:
                raise ValueError("Input CSV contains no data rows.")
            if persons_in_household != int(previous_household["PERSONS"]):
                raise ValueError(
                    f"SERIAL {previous_serial} has {persons_in_household} rows "
                    f"but PERSONS is {previous_household['PERSONS']}."
                )

        os.replace(household_temp, HOUSEHOLD_OUTPUT)
        os.replace(person_temp, PERSON_OUTPUT)
    except BaseException:
        for path in (household_temp, person_temp):
            if path.exists():
                path.unlink()
        raise

    print(f"Wrote {household_count:,} households to {HOUSEHOLD_OUTPUT}")
    print(f"Wrote {person_count:,} people to {PERSON_OUTPUT}")


if __name__ == "__main__":
    main()
