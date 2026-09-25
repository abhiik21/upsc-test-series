# v69 — Students could not press "Start Test"

- **Fixed:** on the test instructions page the Start Test button is switched off until the student ticks "I have read and understood the instructions", but nothing ever switched it back on. No student could start any test (free or premium) from the site. The button now turns on when the box is ticked and off again when it is cleared. Only `student-api.js` changed.
- Checked in a browser with a student account: My Tests -> instructions -> tick box -> Start -> answer, Next/Previous, Mark for Review, Clear, palette, timer -> Submit -> result page with score and solutions.
- Reminder of the other reasons a student cannot start a test: it is saved as Draft (not shown to students), its release date is in the future ("This test opens on ..."), or it is Premium and the student has no active plan ("Unlock with a plan"). The test builder's default access is Premium.
