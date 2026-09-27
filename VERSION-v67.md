# v67 — Navigation clean-up

- The top menu had both a "Free Tests" link and a "Start Free" button, and both opened `free-tests.html`. The duplicate "Free Tests" menu link is removed and the button is renamed **Free Tests** (18 public pages). The footer link, the phone menu entry and the "Start Free Test" buttons inside page content are unchanged.
- **Fixed: the phone menu was empty.** Its contents were built by a script in the page head that ran before the menu existed, so on screens up to 740px wide (where the top menu and button are hidden) the menu button opened an empty panel. The script now waits for the page to load (20 pages).
