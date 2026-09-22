# Error sample

50 of 290 failures from `results/baseline_full.jsonl`, sampled proportionally across databases.

Labels are filled in by hand and collected in `eval/error_labels.json`; run `scripts/06_error_report.py` to aggregate them.

---

## 1. `battle_death#493`  —  column_count_mismatch

**Q:** List the name, date and result of each battle.

```sql
-- gold
SELECT name , date FROM battle

-- predicted
SELECT name, date, result FROM battle
```

| | result |
|---|---|
| gold | 8 row(s)<br>  name | date<br>  'Battle of Adrianople' | '14 April 1205'<br>  'Battle of Serres' | 'June 1205'<br>  'Battle of Rusion' | '31 January 1206'<br>  ... 5 more rows |
| predicted | 8 row(s)<br>  name | date | result<br>  'Battle of Adrianople' | '14 April 1205' | 'Bulgarian victory'<br>  'Battle of Serres' | 'June 1205' | 'Bulgarian victory'<br>  'Battle of Rusion' | '31 January 1206' | 'Bulgarian victory'<br>  ... 5 more rows |

**label:** `TODO`

## 2. `car_1#104`  —  value_mismatch

**Q:** What are the different models for the cards produced after 1980?

```sql
-- gold
SELECT DISTINCT T1.model FROM MODEL_LIST AS T1 JOIN CAR_NAMES AS T2 ON T1.model = T2.model JOIN CARS_DATA AS T3 ON T2.MakeId = T3.id WHERE T3.year > 1980;

-- predicted
SELECT DISTINCT Model FROM model_list WHERE ModelId IN (SELECT Id FROM cars_data WHERE Year > 1980)
```

| | result |
|---|---|
| gold | 21 row(s)<br>  Model<br>  'plymouth'<br>  'buick'<br>  'dodge'<br>  ... 18 more rows |
| predicted | 0 row(s)<br>  Model<br> |

**label:** `TODO`

## 3. `car_1#113`  —  value_mismatch

**Q:** How many car makers are there in france?

```sql
-- gold
SELECT count(*) FROM CAR_MAKERS AS T1 JOIN COUNTRIES AS T2 ON T1.Country = T2.CountryId WHERE T2.CountryName = 'france';

-- predicted
SELECT COUNT(*) FROM car_makers WHERE Country = 'France'
```

| | result |
|---|---|
| gold | 1 row(s)<br>  count(*)<br>  3 |
| predicted | 1 row(s)<br>  COUNT(*)<br>  0 |

**label:** `TODO`

## 4. `car_1#124`  —  value_mismatch

**Q:** What are the names and ids of all countries with at least one car maker?

```sql
-- gold
SELECT T1.CountryName , T1.CountryId FROM COUNTRIES AS T1 JOIN CAR_MAKERS AS T2 ON T1.CountryId = T2.Country GROUP BY T1.CountryId HAVING count(*) >= 1;

-- predicted
SELECT DISTINCT countries.CountryId, countries.CountryName FROM countries JOIN car_makers ON countries.CountryId = car_makers.Country
```

| | result |
|---|---|
| gold | 8 row(s)<br>  CountryName | CountryId<br>  'usa' | 1<br>  'germany' | 2<br>  'france' | 3<br>  ... 5 more rows |
| predicted | 8 row(s)<br>  CountryId | CountryName<br>  1 | 'usa'<br>  2 | 'germany'<br>  3 | 'france'<br>  ... 5 more rows |

**label:** `TODO`

## 5. `car_1#138`  —  value_mismatch

**Q:** What is the average edispl for all volvos?

```sql
-- gold
SELECT avg(T2.edispl) FROM CAR_NAMES AS T1 JOIN CARS_DATA AS T2 ON T1.MakeId = T2.Id WHERE T1.Model = 'volvo';

-- predicted
SELECT AVG(Edispl) FROM cars_data INNER JOIN car_names ON cars_data.Id = car_names.MakeId WHERE car_names.Make = 'Volvo'
```

| | result |
|---|---|
| gold | 1 row(s)<br>  avg(T2.edispl)<br>  133.5 |
| predicted | 1 row(s)<br>  AVG(Edispl)<br>  None |

**label:** `TODO`

## 6. `car_1#148`  —  value_mismatch

**Q:** What is the number of car models created by the car maker American Motor Company?

```sql
-- gold
SELECT count(*) FROM CAR_MAKERS AS T1 JOIN MODEL_LIST AS T2 ON T1.Id = T2.Maker WHERE T1.FullName = 'American Motor Company';

-- predicted
SELECT COUNT(*) FROM model_list WHERE Maker = (SELECT Id FROM car_makers WHERE Maker = 'American Motor Company')
```

| | result |
|---|---|
| gold | 1 row(s)<br>  count(*)<br>  1 |
| predicted | 1 row(s)<br>  COUNT(*)<br>  0 |

**label:** `TODO`

## 7. `car_1#159`  —  value_mismatch

**Q:** How many cars have a larger accelerate than the car with the largest horsepower?

```sql
-- gold
SELECT COUNT(*) FROM CARS_DATA WHERE Accelerate > ( SELECT Accelerate FROM CARS_DATA ORDER BY Horsepower DESC LIMIT 1 );

-- predicted
SELECT COUNT(*) FROM cars_data WHERE Accelerate > (SELECT MAX(Horsepower) FROM cars_data)
```

| | result |
|---|---|
| gold | 1 row(s)<br>  COUNT(*)<br>  39 |
| predicted | 1 row(s)<br>  COUNT(*)<br>  0 |

**label:** `TODO`

## 8. `car_1#168`  —  value_mismatch

**Q:** Among the cars that do not have the minimum horsepower , what are the make ids and names of all those with less than 4 cylinders ?

```sql
-- gold
select t2.makeid , t2.make from cars_data as t1 join car_names as t2 on t1.id = t2.makeid where t1.horsepower > (select min(horsepower) from cars_data) and t1.cylinders < 4;

-- predicted
SELECT car_names.MakeId, car_names.Model FROM car_names JOIN cars_data ON car_names.MakeId = cars_data.Id WHERE cars_data.Cylinders < 4 AND cars_data.Horsepower < (SELECT MIN(Horsepower) FROM cars_data)
```

| | result |
|---|---|
| gold | 3 row(s)<br>  MakeId | Make<br>  79 | 'mazda rx2 coupe'<br>  119 | 'mazda rx3'<br>  251 | 'mazda rx-4' |
| predicted | 0 row(s)<br>  MakeId | Model<br> |

**label:** `TODO`

## 9. `car_1#94`  —  value_mismatch

**Q:** What is the full name of each car maker, along with its id and how many models it produces?

```sql
-- gold
SELECT T1.FullName , T1.Id , count(*) FROM CAR_MAKERS AS T1 JOIN MODEL_LIST AS T2 ON T1.Id = T2.Maker GROUP BY T1.Id;

-- predicted
SELECT cm.Id, cm.FullName, COUNT(ml.Model) AS ModelCount FROM car_makers cm JOIN model_list ml ON cm.Id = ml.Maker GROUP BY cm.Id, cm.FullName
```

| | result |
|---|---|
| gold | 22 row(s)<br>  FullName | Id | count(*)<br>  'American Motor Company' | 1 | 1<br>  'Volkswagen' | 2 | 2<br>  'BMW' | 3 | 1<br>  ... 19 more rows |
| predicted | 22 row(s)<br>  Id | FullName | ModelCount<br>  1 | 'American Motor Company' | 1<br>  2 | 'Volkswagen' | 2<br>  3 | 'BMW' | 1<br>  ... 19 more rows |

**label:** `TODO`

## 10. `concert_singer#16`  —  value_mismatch

**Q:** What is the maximum capacity and the average of all stadiums ?

```sql
-- gold
select max(capacity), average from stadium

-- predicted
SELECT MAX(Capacity) AS Max_Capacity, AVG(Capacity) AS Average_Capacity FROM stadium
```

| | result |
|---|---|
| gold | 1 row(s)<br>  max(capacity) | Average<br>  52500 | 730 |
| predicted | 1 row(s)<br>  Max_Capacity | Average_Capacity<br>  52500 | 10621.666666666666 |

**label:** `TODO`

## 11. `course_teach#387`  —  value_mismatch

**Q:** List the name of teachers whose hometown is not `` Little Lever Urban District '' .

```sql
-- gold
select name from teacher where hometown != "little lever urban district"

-- predicted
SELECT Name FROM teacher WHERE Hometown != 'Little Lever Urban District'
```

| | result |
|---|---|
| gold | 7 row(s)<br>  Name<br>  'Joseph Huts'<br>  'Gustaaf Deloor'<br>  'Vicente Carretero'<br>  ... 4 more rows |
| predicted | 6 row(s)<br>  Name<br>  'Joseph Huts'<br>  'Gustaaf Deloor'<br>  'Vicente Carretero'<br>  ... 3 more rows |

**label:** `TODO`

## 12. `cre_Doc_Template_Mgt#308`  —  value_mismatch

**Q:** Count the number of documents that use the PPT template type.

```sql
-- gold
SELECT count(*) FROM Documents AS T1 JOIN Templates AS T2 ON T1.Template_ID = T2.Template_ID WHERE T2.Template_Type_Code = 'PPT'

-- predicted
SELECT COUNT(DISTINCT D.Document_ID) AS Document_Count FROM Documents D JOIN Templates T ON D.Template_ID = T.Template_ID WHERE T.Template_Type_Code = (SELECT Template_Type_Code FROM Ref_Template_Types WHERE Template_Type_Description = 'PPT')
```

| | result |
|---|---|
| gold | 1 row(s)<br>  count(*)<br>  3 |
| predicted | 1 row(s)<br>  Document_Count<br>  0 |

**label:** `TODO`

## 13. `cre_Doc_Template_Mgt#343`  —  column_count_mismatch

**Q:** Which template type code is used by most number of documents?

```sql
-- gold
SELECT T1.template_type_code FROM Templates AS T1 JOIN Documents AS T2 ON T1.template_id = T2.template_id GROUP BY T1.template_type_code ORDER BY count(*) DESC LIMIT 1

-- predicted
SELECT Template_Type_Code, COUNT(DISTINCT Document_ID) AS Document_Count FROM Documents JOIN Templates ON Documents.Template_ID = Templates.Template_ID GROUP BY Template_Type_Code ORDER BY Document_Count DESC LIMIT 1
```

| | result |
|---|---|
| gold | 1 row(s)<br>  Template_Type_Code<br>  'BK' |
| predicted | 1 row(s)<br>  Template_Type_Code | Document_Count<br>  'BK' | 5 |

**label:** `TODO`

## 14. `cre_Doc_Template_Mgt#362`  —  column_count_mismatch

**Q:** What are the details for the paragraph that includes the text 'Korea ' ?

```sql
-- gold
select other_details from paragraphs where paragraph_text like 'korea'

-- predicted
SELECT * FROM Paragraphs WHERE Paragraph_Text LIKE '%Korea%'
```

| | result |
|---|---|
| gold | 2 row(s)<br>  Other_Details<br>  None<br>  None |
| predicted | 2 row(s)<br>  Paragraph_ID | Document_ID | Paragraph_Text | Other_Details<br>  7 | 2394 | 'Korea' | None<br>  510442723 | 2394 | 'Korea' | None |

**label:** `TODO`

## 15. `dog_kennels#925`  —  value_mismatch

**Q:** What are the names of the dogs for which the owner has not spend more than 1000 for treatment ?

```sql
-- gold
select name from dogs where dog_id not in ( select dog_id from treatments group by dog_id having sum(cost_of_treatment) > 1000 )

-- predicted
SELECT d.name FROM Dogs d WHERE d.owner_id NOT IN ( SELECT t.dog_id FROM Treatments t GROUP BY t.dog_id HAVING SUM(t.cost_of_treatment) > 1000 )
```

| | result |
|---|---|
| gold | 12 row(s)<br>  name<br>  'Kacey'<br>  'Hipolito'<br>  'Mavis'<br>  ... 9 more rows |
| predicted | 11 row(s)<br>  name<br>  'Kacey'<br>  'Hipolito'<br>  'Mavis'<br>  ... 8 more rows |

**label:** `TODO`

## 16. `dog_kennels#943`  —  exec_error:sql_error

**Q:** Find the id and cell phone of the professionals who operate two or more types of treatments.

```sql
-- gold
SELECT T1.professional_id , T1.cell_number FROM Professionals AS T1 JOIN Treatments AS T2 ON T1.professional_id = T2.professional_id GROUP BY T1.professional_id HAVING count(*) >= 2

-- predicted
SELECT professional_id, cell_number FROM Treatments GROUP BY professional_id HAVING COUNT(DISTINCT treatment_type_code) >= 2
```

| | result |
|---|---|
| gold | 6 row(s)<br>  professional_id | cell_number<br>  4 | '00230569697'<br>  6 | '139-321-7313'<br>  8 | '328.842.3792'<br>  ... 3 more rows |
| predicted | n/a |

**label:** `TODO`

## 17. `dog_kennels#959`  —  value_mismatch

**Q:** Find the arriving date and the departing date of the dogs that received a treatment.

```sql
-- gold
SELECT DISTINCT T1.date_arrived , T1.date_departed FROM Dogs AS T1 JOIN Treatments AS T2 ON T1.dog_id = T2.dog_id

-- predicted
SELECT d.date_arrived, d.date_departed FROM Dogs d JOIN Treatments t ON d.dog_id = t.dog_id
```

| | result |
|---|---|
| gold | 9 row(s)<br>  date_arrived | date_departed<br>  '2017-06-18 19:45:38' | '2018-03-24 23:48:59'<br>  '2017-04-20 00:58:55' | '2018-03-24 19:12:22'<br>  '2017-12-22 05:02:02' | '2018-03-25 02:11:32'<br>  ... 6 more rows |
| predicted | 15 row(s)<br>  date_arrived | date_departed<br>  '2017-06-18 19:45:38' | '2018-03-24 23:48:59'<br>  '2017-04-20 00:58:55' | '2018-03-24 19:12:22'<br>  '2017-06-18 19:45:38' | '2018-03-24 23:48:59'<br>  ... 12 more rows |

**label:** `TODO`

## 18. `employee_hire_evaluation#263`  —  value_mismatch

**Q:** What is the number of employees from each city?

```sql
-- gold
SELECT count(*) , city FROM employee GROUP BY city

-- predicted
SELECT City, COUNT(*) AS Number_of_Employees FROM employee GROUP BY City
```

| | result |
|---|---|
| gold | 5 row(s)<br>  count(*) | City<br>  3 | 'Bath'<br>  3 | 'Bristol'<br>  1 | 'Leicester'<br>  ... 2 more rows |
| predicted | 5 row(s)<br>  City | Number_of_Employees<br>  'Bath' | 3<br>  'Bristol' | 3<br>  'Leicester' | 1<br>  ... 2 more rows |

**label:** `TODO`

## 19. `flight_2#180`  —  value_mismatch

**Q:** What country is Jetblue Airways affiliated with?

```sql
-- gold
SELECT Country FROM AIRLINES WHERE Airline = "JetBlue Airways"

-- predicted
SELECT Country FROM airlines WHERE Airline = 'Jetblue Airways'
```

| | result |
|---|---|
| gold | 1 row(s)<br>  Country<br>  'USA' |
| predicted | 0 row(s)<br>  Country<br> |

**label:** `TODO`

## 20. `flight_2#228`  —  value_mismatch

**Q:** Give the code of the airport with the least flights.

```sql
-- gold
SELECT T1.AirportCode FROM AIRPORTS AS T1 JOIN FLIGHTS AS T2 ON T1.AirportCode = T2.DestAirport OR T1.AirportCode = T2.SourceAirport GROUP BY T1.AirportCode ORDER BY count(*) LIMIT 1

-- predicted
SELECT SourceAirport FROM flights GROUP BY SourceAirport ORDER BY COUNT(*) ASC LIMIT 1
```

| | result |
|---|---|
| gold | 0 row(s)<br>  AirportCode<br> |
| predicted | 1 row(s)<br>  SourceAirport<br>  ' AID' |

**label:** `TODO`

## 21. `museum_visit#417`  —  value_mismatch

**Q:** What are the opening year and staff number of the museum named Plaza Museum?

```sql
-- gold
SELECT Num_of_Staff , Open_Year FROM museum WHERE name = 'Plaza Museum'

-- predicted
SELECT Open_Year, Num_of_Staff FROM museum WHERE Name = 'Plaza Museum'
```

| | result |
|---|---|
| gold | 1 row(s)<br>  Num_of_Staff | Open_Year<br>  62 | '2000' |
| predicted | 1 row(s)<br>  Open_Year | Num_of_Staff<br>  '2000' | 62 |

**label:** `TODO`

## 22. `network_1#867`  —  column_count_mismatch

**Q:** What is the grade of each high schooler?

```sql
-- gold
SELECT grade FROM Highschooler

-- predicted
SELECT ID, grade FROM Highschooler
```

| | result |
|---|---|
| gold | 16 row(s)<br>  grade<br>  9<br>  9<br>  9<br>  ... 13 more rows |
| predicted | 16 row(s)<br>  ID | grade<br>  1510 | 9<br>  1689 | 9<br>  1381 | 9<br>  ... 13 more rows |

**label:** `TODO`

## 23. `network_1#900`  —  value_mismatch

**Q:** Show name of all students who have some friends and also are liked by someone else.

```sql
-- gold
SELECT T2.name FROM Friend AS T1 JOIN Highschooler AS T2 ON T1.student_id = T2.id INTERSECT SELECT T2.name FROM Likes AS T1 JOIN Highschooler AS T2 ON T1.liked_id = T2.id

-- predicted
SELECT DISTINCT H.name FROM Highschooler H JOIN Friend F ON H.ID = F.student_id JOIN Likes L ON H.ID = L.student_id
```

| | result |
|---|---|
| gold | 8 row(s)<br>  name<br>  'Alexis'<br>  'Cassandra'<br>  'Gabriel'<br>  ... 5 more rows |
| predicted | 7 row(s)<br>  name<br>  'Gabriel'<br>  'Cassandra'<br>  'Andrew'<br>  ... 4 more rows |

**label:** `TODO`

## 24. `orchestra#851`  —  column_count_mismatch

**Q:** What are the major record formats of orchestras, sorted by their frequency?

```sql
-- gold
SELECT Major_Record_Format FROM orchestra GROUP BY Major_Record_Format ORDER BY COUNT(*) ASC

-- predicted
SELECT Major_Record_Format, COUNT(*) as Frequency FROM orchestra GROUP BY Major_Record_Format ORDER BY Frequency DESC
```

| | result |
|---|---|
| gold | 3 row(s)<br>  Major_Record_Format<br>  'CD / LP'<br>  'DVD'<br>  'CD' |
| predicted | 3 row(s)<br>  Major_Record_Format | Frequency<br>  'CD' | 10<br>  'DVD' | 1<br>  'CD / LP' | 1 |

**label:** `TODO`

## 25. `pets_1#49`  —  value_mismatch

**Q:** Find the maximum weight for each type of pet. List the maximum weight and pet type.

```sql
-- gold
SELECT max(weight) , petType FROM pets GROUP BY petType

-- predicted
SELECT PetType, MAX(weight) AS MaxWeight FROM Pets GROUP BY PetType
```

| | result |
|---|---|
| gold | 2 row(s)<br>  max(weight) | PetType<br>  12.0 | 'cat'<br>  13.4 | 'dog' |
| predicted | 2 row(s)<br>  PetType | MaxWeight<br>  'cat' | 12.0<br>  'dog' | 13.4 |

**label:** `TODO`

## 26. `pets_1#72`  —  value_mismatch

**Q:** What is the average and maximum age for each pet type?

```sql
-- gold
SELECT avg(pet_age) , max(pet_age) , pettype FROM pets GROUP BY pettype

-- predicted
SELECT PetType, AVG(pet_age) AS average_age, MAX(pet_age) AS maximum_age FROM Pets GROUP BY PetType
```

| | result |
|---|---|
| gold | 2 row(s)<br>  avg(pet_age) | max(pet_age) | PetType<br>  3.0 | 3 | 'cat'<br>  1.5 | 2 | 'dog' |
| predicted | 2 row(s)<br>  PetType | average_age | maximum_age<br>  'cat' | 3.0 | 3<br>  'dog' | 1.5 | 2 |

**label:** `TODO`

## 27. `poker_player#675`  —  column_count_mismatch

**Q:** What is the most common nationality of people?

```sql
-- gold
SELECT Nationality FROM people GROUP BY Nationality ORDER BY COUNT(*) DESC LIMIT 1

-- predicted
SELECT Nationality, COUNT(*) as Count FROM people GROUP BY Nationality ORDER BY Count DESC LIMIT 1
```

| | result |
|---|---|
| gold | 1 row(s)<br>  Nationality<br>  'Russia' |
| predicted | 1 row(s)<br>  Nationality | Count<br>  'Russia' | 6 |

**label:** `TODO`

## 28. `real_estate_properties#1032`  —  value_mismatch

**Q:** Show the property type descriptions of properties belonging to that code.

```sql
-- gold
SELECT T2.property_type_description FROM Properties AS T1 JOIN Ref_Property_Types AS T2 ON T1.property_type_code = T2.property_type_code GROUP BY T1.property_type_code

-- predicted
SELECT RPT.property_type_description FROM Properties P JOIN Ref_Property_Types RPT ON P.property_type_code = RPT.property_type_code
```

| | result |
|---|---|
| gold | 5 row(s)<br>  property_type_description<br>  'Apartment, Flat, Condo, etc.'<br>  'Field, Meadow.'<br>  'House, Bungalow, etc.'<br>  ... 2 more rows |
| predicted | 15 row(s)<br>  property_type_description<br>  'House, Bungalow, etc.'<br>  'House, Bungalow, etc.'<br>  'Other, to be determined.'<br>  ... 12 more rows |

**label:** `TODO`

## 29. `singer#1007`  —  value_mismatch

**Q:** What are the names of the singers who are not French citizens?

```sql
-- gold
SELECT Name FROM singer WHERE Citizenship != "France"

-- predicted
SELECT Name FROM singer WHERE Citizenship != 'French'
```

| | result |
|---|---|
| gold | 7 row(s)<br>  Name<br>  'Christy Walton'<br>  'Alice Walton'<br>  'Iris Fontbona'<br>  ... 4 more rows |
| predicted | 8 row(s)<br>  Name<br>  'Liliane Bettencourt'<br>  'Christy Walton'<br>  'Alice Walton'<br>  ... 5 more rows |

**label:** `TODO`

## 30. `student_transcripts_tracking#511`  —  value_mismatch

**Q:** How is the math course described?

```sql
-- gold
SELECT course_description FROM Courses WHERE course_name = 'math'

-- predicted
SELECT course_description FROM Courses WHERE course_name = 'Math'
```

| | result |
|---|---|
| gold | 1 row(s)<br>  course_description<br>  'q' |
| predicted | 0 row(s)<br>  course_description<br> |

**label:** `TODO`

## 31. `student_transcripts_tracking#529`  —  value_mismatch

**Q:** What is the semester which most student registered in? Show both the name and the id.

```sql
-- gold
SELECT T1.semester_name , T1.semester_id FROM Semesters AS T1 JOIN Student_Enrolment AS T2 ON T1.semester_id = T2.semester_id GROUP BY T1.semester_id ORDER BY count(*) DESC LIMIT 1

-- predicted
SELECT semester_id, semester_name FROM Semesters WHERE semester_id IN (SELECT semester_id FROM Student_Enrolment GROUP BY semester_id ORDER BY COUNT(student_enrolment_id) DESC LIMIT 1)
```

| | result |
|---|---|
| gold | 1 row(s)<br>  semester_name | semester_id<br>  'summer 2010' | 2 |
| predicted | 1 row(s)<br>  semester_id | semester_name<br>  2 | 'summer 2010' |

**label:** `TODO`

## 32. `student_transcripts_tracking#537`  —  column_count_mismatch

**Q:** Find the kind of program which most number of students are enrolled in?

```sql
-- gold
SELECT T1.degree_summary_name FROM Degree_Programs AS T1 JOIN Student_Enrolment AS T2 ON T1.degree_program_id = T2.degree_program_id GROUP BY T1.degree_summary_name ORDER BY count(*) DESC LIMIT 1

-- predicted
SELECT dp.degree_summary_name, COUNT(se.student_enrolment_id) AS student_count FROM Degree_Programs dp JOIN Student_Enrolment se ON dp.degree_program_id = se.degree_program_id GROUP BY dp.degree_summary_name ORDER BY student_count DESC LIMIT 1
```

| | result |
|---|---|
| gold | 1 row(s)<br>  degree_summary_name<br>  'Bachelor' |
| predicted | 1 row(s)<br>  degree_summary_name | student_count<br>  'Bachelor' | 8 |

**label:** `TODO`

## 33. `student_transcripts_tracking#550`  —  value_mismatch

**Q:** What are the last name of the students who live in North Carolina but have not registered in any degree programs?

```sql
-- gold
SELECT T1.last_name FROM Students AS T1 JOIN Addresses AS T2 ON T1.current_address_id = T2.address_id WHERE T2.state_province_county = 'NorthCarolina' EXCEPT SELECT DISTINCT T3.last_name FROM Students AS T3 JOIN Student_Enrolment AS T4 ON T3.student_id = T4.student_id

-- predicted
SELECT last_name FROM Students WHERE current_address_id IN (SELECT address_id FROM Addresses WHERE state_province_county = 'North Carolina') AND student_id NOT IN (SELECT student_id FROM Student_Enrolment)
```

| | result |
|---|---|
| gold | 2 row(s)<br>  last_name<br>  'Gleichner'<br>  'Weimann' |
| predicted | 0 row(s)<br>  last_name<br> |

**label:** `TODO`

## 34. `student_transcripts_tracking#571`  —  value_mismatch

**Q:** How many times at most can a course enrollment result show in different transcripts? Also show the course enrollment id.

```sql
-- gold
SELECT count(*) , student_course_id FROM Transcript_Contents GROUP BY student_course_id ORDER BY count(*) DESC LIMIT 1

-- predicted
SELECT student_course_id, COUNT(transcript_id) AS transcript_count FROM Transcript_Contents GROUP BY student_course_id ORDER BY transcript_count DESC LIMIT 1
```

| | result |
|---|---|
| gold | 1 row(s)<br>  count(*) | student_course_id<br>  4 | 0 |
| predicted | 1 row(s)<br>  student_course_id | transcript_count<br>  0 | 4 |

**label:** `TODO`

## 35. `student_transcripts_tracking#578`  —  column_count_mismatch

**Q:** What are the different addresses that have students living there?

```sql
-- gold
SELECT count(DISTINCT current_address_id) FROM Students

-- predicted
SELECT DISTINCT a.* FROM Addresses a JOIN Students s ON a.address_id = s.current_address_id OR a.address_id = s.permanent_address_id
```

| | result |
|---|---|
| gold | 1 row(s)<br>  count(DISTINCT current_address_id)<br>  9 |
| predicted | 14 row(s)<br>  address_id | line_1 | line_2 | line_3 | city | zip_postcode | state_province_county | country | other_address_details<br>  10 | '7081 Eda Center' | 'Suite 830' | None | 'Rutherfordtown' | '839' | 'Delaware' | 'Kyrgyz Republic' | None<br>  15 | '74046 Metz Walk Apt. 113' | 'Suite 674' | None | 'Port Oceane' | '669' | 'Wyoming' | 'Norfolk Island' | None<br>  5 | '858 Peggie Bypass Suite 212' | 'Apt. 335' | None | 'Michelleburgh' | '446' | 'Hawaii' | 'Haiti' | None<br>  ... 11 more rows |

**label:** `TODO`

## 36. `tvshow#587`  —  column_count_mismatch

**Q:** List all cartoon directed by "Ben Jones".

```sql
-- gold
SELECT Title FROM Cartoon WHERE Directed_by = "Ben Jones";

-- predicted
SELECT * FROM Cartoon WHERE Directed_by = 'Ben Jones'
```

| | result |
|---|---|
| gold | 4 row(s)<br>  Title<br>  'The Rise of the Blue Beetle!'<br>  'Day of the Dark Knight!'<br>  'Dawn of the Dead Man!'<br>  ... 1 more rows |
| predicted | 4 row(s)<br>  id | Title | Directed_by | Written_by | Original_air_date | Production_code | Channel<br>  1.0 | 'The Rise of the Blue Beetle!' | 'Ben Jones' | 'Michael Jelenic' | 'November14,2008' | 101.0 | '700'<br>  4.0 | 'Day of the Dark Knight!' | 'Ben Jones' | 'J. M. DeMatteis' | 'January2,2009' | 104.0 | '704'<br>  7.0 | 'Dawn of the Dead Man!' | 'Ben Jones' | 'Steven Melching' | 'January16,2009' | 107.0 | '707'<br>  ... 1 more rows |

**label:** `TODO`

## 37. `tvshow#628`  —  value_mismatch

**Q:** How many cartoons did each director create?

```sql
-- gold
SELECT count(*) , Directed_by FROM cartoon GROUP BY Directed_by

-- predicted
SELECT Directed_by, COUNT(*) AS Cartoon_Count FROM Cartoon GROUP BY Directed_by
```

| | result |
|---|---|
| gold | 3 row(s)<br>  count(*) | Directed_by<br>  4 | 'Ben Jones'<br>  4 | 'Brandon Vietti'<br>  4 | 'Michael Chang' |
| predicted | 3 row(s)<br>  Directed_by | Cartoon_Count<br>  'Ben Jones' | 4<br>  'Brandon Vietti' | 4<br>  'Michael Chang' | 4 |

**label:** `TODO`

## 38. `tvshow#637`  —  value_mismatch

**Q:** Find the series name and country of the tv channel that is playing some cartoons directed by Ben Jones and Michael Chang?

```sql
-- gold
SELECT T1.series_name , T1.country FROM TV_Channel AS T1 JOIN cartoon AS T2 ON T1.id = T2.Channel WHERE T2.directed_by = 'Michael Chang' INTERSECT SELECT T1.series_name , T1.country FROM TV_Channel AS T1 JOIN cartoon AS T2 ON T1.id = T2.Channel WHERE T2.directed_by = 'Ben Jones'

-- predicted
SELECT TV_Channel.series_name, TV_Channel.Country FROM Cartoon JOIN TV_Channel ON Cartoon.Channel = TV_Channel.id WHERE Cartoon.Directed_by IN ('Ben Jones', 'Michael Chang')
```

| | result |
|---|---|
| gold | 1 row(s)<br>  series_name | Country<br>  'MTV Dance' | 'United Kingdom' |
| predicted | 8 row(s)<br>  series_name | Country<br>  'Sky Radio' | 'Italy'<br>  'Music Box Italia' | 'Italy'<br>  'MTV Hits' | 'Italy'<br>  ... 5 more rows |

**label:** `TODO`

## 39. `voter_1#687`  —  value_mismatch

**Q:** How many states are there?

```sql
-- gold
SELECT count(*) FROM area_code_state

-- predicted
SELECT COUNT(DISTINCT state) FROM AREA_CODE_STATE
```

| | result |
|---|---|
| gold | 1 row(s)<br>  count(*)<br>  305 |
| predicted | 1 row(s)<br>  COUNT(DISTINCT state)<br>  51 |

**label:** `TODO`

## 40. `world_1#704`  —  value_mismatch

**Q:** How many countries have a republic as their form of government?

```sql
-- gold
SELECT count(*) FROM country WHERE GovernmentForm = "Republic"

-- predicted
SELECT COUNT(*) FROM country WHERE GovernmentForm LIKE '%republic%'
```

| | result |
|---|---|
| gold | 1 row(s)<br>  count(*)<br>  122 |
| predicted | 1 row(s)<br>  COUNT(*)<br>  143 |

**label:** `TODO`

## 41. `world_1#723`  —  value_mismatch

**Q:** How many people live in Asia, and what is the largest GNP among them?

```sql
-- gold
SELECT sum(Population) , max(GNP) FROM country WHERE Continent = "Asia"

-- predicted
SELECT SUM(c.Population) AS TotalPopulation, MAX(co.GNP) AS LargestGNP FROM country co JOIN city c ON co.Code = c.CountryCode WHERE co.Continent = 'Asia'
```

| | result |
|---|---|
| gold | 1 row(s)<br>  sum(Population) | max(GNP)<br>  3705025700 | 3787042.0 |
| predicted | 1 row(s)<br>  TotalPopulation | LargestGNP<br>  697604103 | 3787042.0 |

**label:** `TODO`

## 42. `world_1#743`  —  column_count_mismatch

**Q:** Which continent speaks the most languages?

```sql
-- gold
SELECT T1.Continent FROM country AS T1 JOIN countrylanguage AS T2 ON T1.Code = T2.CountryCode GROUP BY T1.Continent ORDER BY COUNT(*) DESC LIMIT 1

-- predicted
SELECT c.Continent, COUNT(cl.Language) AS LanguageCount FROM country c JOIN countrylanguage cl ON c.Code = cl.CountryCode GROUP BY c.Continent ORDER BY LanguageCount DESC LIMIT 1
```

| | result |
|---|---|
| gold | 1 row(s)<br>  Continent<br>  'Africa' |
| predicted | 1 row(s)<br>  Continent | LanguageCount<br>  'Africa' | 310 |

**label:** `TODO`

## 43. `world_1#761`  —  column_count_mismatch

**Q:** What is the most populace city that speaks English?

```sql
-- gold
SELECT T1.Name , T1.Population FROM city AS T1 JOIN countrylanguage AS T2 ON T1.CountryCode = T2.CountryCode WHERE T2.Language = "English" ORDER BY T1.Population DESC LIMIT 1

-- predicted
SELECT Name FROM city WHERE ID = (SELECT ID FROM city WHERE CountryCode IN (SELECT CountryCode FROM countrylanguage WHERE Language = 'English') ORDER BY Population DESC LIMIT 1)
```

| | result |
|---|---|
| gold | 1 row(s)<br>  Name | Population<br>  'New York' | 8008278 |
| predicted | 1 row(s)<br>  Name<br>  'New York' |

**label:** `TODO`

## 44. `world_1#783`  —  value_mismatch

**Q:** Return the codes of countries that do not speak English and do not have Republics for governments.

```sql
-- gold
SELECT Code FROM country WHERE GovernmentForm != "Republic" EXCEPT SELECT CountryCode FROM countrylanguage WHERE LANGUAGE = "English"

-- predicted
SELECT c.Code FROM country c WHERE c.Code NOT IN (SELECT cl.CountryCode FROM countrylanguage cl WHERE cl.Language = 'English') AND c.GovernmentForm NOT LIKE '%Republic%'
```

| | result |
|---|---|
| gold | 69 row(s)<br>  Code<br>  'AFG'<br>  'AND'<br>  'ARE'<br>  ... 66 more rows |
| predicted | 49 row(s)<br>  Code<br>  'AFG'<br>  'AND'<br>  'ARE'<br>  ... 46 more rows |

**label:** `TODO`

## 45. `world_1#793`  —  value_mismatch

**Q:** What are the names of countries that speak more than 2 languages, as well as how many languages they speak?

```sql
-- gold
SELECT COUNT(T2.Language) , T1.Name FROM country AS T1 JOIN countrylanguage AS T2 ON T1.Code = T2.CountryCode GROUP BY T1.Name HAVING COUNT(*) > 2

-- predicted
SELECT c.Name, COUNT(cl.Language) AS LanguageCount FROM country c JOIN countrylanguage cl ON c.Code = cl.CountryCode GROUP BY c.Code HAVING COUNT(cl.Language) > 2
```

| | result |
|---|---|
| gold | 149 row(s)<br>  COUNT(T2.Language) | Name<br>  5 | 'Afghanistan'<br>  3 | 'Albania'<br>  3 | 'American Samoa'<br>  ... 146 more rows |
| predicted | 149 row(s)<br>  Name | LanguageCount<br>  'Aruba' | 4<br>  'Afghanistan' | 5<br>  'Angola' | 9<br>  ... 146 more rows |

**label:** `TODO`

## 46. `world_1#811`  —  value_mismatch

**Q:** Give the total population and average surface area corresponding to countries in North America that have a surface area greater than 3000 .

```sql
-- gold
select sum(population) , avg(surfacearea) from country where continent = "north america" and surfacearea > 3000

-- predicted
SELECT SUM(Population) AS TotalPopulation, AVG(SurfaceArea) AS AverageSurfaceArea FROM country WHERE Region = 'North America' AND SurfaceArea > 3000
```

| | result |
|---|---|
| gold | 1 row(s)<br>  sum(population) | avg(surfacearea)<br>  None | None |
| predicted | 1 row(s)<br>  TotalPopulation | AverageSurfaceArea<br>  309560000 | 7166740.0 |

**label:** `TODO`

## 47. `wta_1#439`  —  value_mismatch

**Q:** Find the highest rank of losers in all matches.

```sql
-- gold
SELECT min(loser_rank) FROM matches

-- predicted
SELECT MAX(loser_rank) AS highest_loser_rank FROM matches
```

| | result |
|---|---|
| gold | 1 row(s)<br>  min(loser_rank)<br>  1 |
| predicted | 1 row(s)<br>  highest_loser_rank<br>  112 |

**label:** `TODO`

## 48. `wta_1#460`  —  value_mismatch

**Q:** What is the first name and country code of the player with the most tours?

```sql
-- gold
SELECT T1.country_code , T1.first_name FROM players AS T1 JOIN rankings AS T2 ON T1.player_id = T2.player_id ORDER BY T2.tours DESC LIMIT 1

-- predicted
SELECT first_name, country_code FROM players WHERE player_id = (SELECT player_id FROM rankings ORDER BY tours DESC LIMIT 1)
```

| | result |
|---|---|
| gold | 1 row(s)<br>  country_code | first_name<br>  'USA' | 'Julia' |
| predicted | 1 row(s)<br>  first_name | country_code<br>  'Julia' | 'USA' |

**label:** `TODO`

## 49. `wta_1#471`  —  value_mismatch

**Q:** Find the total ranking points for each player and their first name.

```sql
-- gold
SELECT sum(ranking_points) , T1.first_name FROM players AS T1 JOIN rankings AS T2 ON T1.player_id = T2.player_id GROUP BY T1.first_name

-- predicted
SELECT p.first_name, SUM(r.ranking_points) AS total_ranking_points FROM players p JOIN rankings r ON p.player_id = r.player_id GROUP BY p.player_id, p.first_name
```

| | result |
|---|---|
| gold | 1580 row(s)<br>  sum(ranking_points) | first_name<br>  68 | 'Aastha'<br>  304 | 'Abbi'<br>  5214 | 'Abbie'<br>  ... 1577 more rows |
| predicted | 2775 row(s)<br>  first_name | total_ranking_points<br>  'Mirjana' | 331434<br>  'Tamarine' | 594281<br>  'Katarina' | 437442<br>  ... 2772 more rows |

**label:** `TODO`

## 50. `wta_1#481`  —  value_mismatch

**Q:** Find the number of matches happened in each year.

```sql
-- gold
SELECT count(*) , YEAR FROM matches GROUP BY YEAR

-- predicted
SELECT year, COUNT(*) AS match_count FROM matches GROUP BY year
```

| | result |
|---|---|
| gold | 3 row(s)<br>  count(*) | year<br>  12 | 2013<br>  158 | 2016<br>  108 | 2017 |
| predicted | 3 row(s)<br>  year | match_count<br>  2013 | 12<br>  2016 | 158<br>  2017 | 108 |

**label:** `TODO`
