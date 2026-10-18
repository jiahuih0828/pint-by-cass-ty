# Pint — shared finance tracker (learning copy)

A small Python website for Cass and Ty to keep the same business records from different devices. It has a separate owner login for each of you and a public customer welcome page at `/order`. Customer requests appear in the private tracker, where you manually check PayNow or bank transfer before confirming the order and adding it to sales.

The app is branded with the Pint logo. Customers can put multiple flavours and sizes in one order, attach payment screenshots, request available loyalty rewards, and revisit their private order link to check pickup status. Owners review payment, choose a matching batch for each flavour, record when an order is ready, and can change it back if needed. Menu photo spaces are placeholders because no flavour product photos have been added yet.

## What each file does

- `app.py` — website logic: checks logins, processes forms, calculates totals, and reads/writes records.
- `templates/index.html` — the tracker pages after sign-in.
- `templates/login.html` — the owner sign-in page.
- `templates/order_home.html`, `templates/order_menu.html`, `templates/order.html`, `templates/order_prices.html`, `templates/order_nutrition.html`, and `templates/order_received.html` — customer welcome, flavour menu, checkout, prices, recipe nutrition, and order status pages. The menu has photo spaces ready for your own flavour pictures. The public welcome page does not display the owner sign-in link.
- Customer payment screenshots are cleaned and stored privately under `instance/payment_receipts/` locally; they are never placed in `static/` or uploaded to GitHub.
- `static/style.css` and `static/logo.jpg` — the design and business logo.
- `setup_auth.py` — privately creates the two partner password hashes.
- `requirements.txt` — Python add-ons the app needs.
- `.env` — local private settings, created by `setup_auth.py`. Do not share this file.
- `instance/tracker.sqlite3` — local database, created automatically when the app starts.

## Updating an existing copy

Download the latest ZIP and copy the updated `app.py`, the complete `templates/` folder, and `static/style.css` into your existing project, replacing those code files. Keep your existing `.env` and `instance/` folder in place; `.env` has your private sign-in settings, and `instance/` contains your records and payment screenshots. Restart the app in VS Code after copying the files. On first start, old batches are kept and count as one pint with eight scoops each.

Recipe tracking now saves the mix-in version on every batch. Your existing Cookies and cream batches are automatically marked “Oreo without filling,” and existing Chocolate batches “With Nutella biscuit.” For a future batch, record the recipe actually used. Customers see a choice only when multiple versions are available in prepared batches; when only one version is available, the page says it is already blended in. The public pages use the Pint logo on its own in the header. Owners can delete a batch together with its linked costs and sales, unless a confirmed customer order still uses that batch; cancel that order first.

## Try it on your own computer first

Open the project folder in VS Code, then open **Terminal → New Terminal**. On macOS, type these lines one at a time:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python setup_auth.py
python3 app.py
```

The setup step asks you to create separate passwords for Cass and Ty. Use at least 12 characters and keep them private. Then open <http://127.0.0.1:5000> and sign in. Leave the terminal window running while you use it. Press **Control+C** in that window to stop the local website.

Open <http://127.0.0.1:5000/order> to preview the public customer welcome page. It links to the order form, flavour menu, prices, and nutrition facts. The owners can enable or disable pickup/event orders and scoop/personal-size/pint sales from the private Customer page tab. Before sharing it online, add your own transfer directions to `.env`, for example:

```text
PAYMENT_INSTRUCTIONS="PayNow to [your business payment details]. Add the order reference shown above as the transfer message."
```

Replace the bracketed words privately with the payment method/details you want customers to use. The app does not collect card details or verify bank transfers automatically.

This local tryout is still only on your computer. Your partner cannot connect to it from somewhere else yet.

## Make it available to both partners online

The included hosting steps use Render. The site gets an internet address, but its pages ask for your partner name and password. Use a private GitHub repository so the source files are not publicly listed. Do not upload `.env`, passwords, or your database file to GitHub.

**Important cost note:** the database must be saved on persistent storage or it can disappear after a restart or update. Render says persistent disks require a paid web service. Check the current price shown in your Render account before creating the service. Render's free web services can sleep, and their local files do not persist across restarts. See [Render's Flask guide](https://render.com/docs/deploy-flask) and [persistent disk guide](https://render.com/docs/disks).

1. Create a private repository on GitHub and upload the project files. The `.gitignore` file excludes `.env`, the local database, and the virtual environment. Check the files list before uploading.
2. In Render, choose **New → Web Service**, connect your private GitHub repository, and select the paid plan that supports persistent disks.
3. Use `pip install -r requirements.txt` as the build command and `gunicorn app:app` as the start command.
4. In the service settings, add a persistent disk with mount path `/var/data` (1 GB is plenty to start for this small tracker).
5. In Render's private environment settings, add these settings:

   | Name | Value |
   |---|---|
   | `APP_ENV` | `production` |
   | `TRACKER_DATABASE` | `/var/data/tracker.sqlite3` |
   | `PAYMENT_RECEIPTS_FOLDER` | `/var/data/payment_receipts` |
| `FLAVOUR_PHOTOS_FOLDER` | `/var/data/flavour_photos` (keeps uploaded flavour pictures after restarts) |
   | `FLASK_SECRET_KEY` | Copy the value created by `setup_auth.py` in your local `.env` file. |
   | `CASS_PASSWORD_HASH` | Copy the matching hash from `.env`. |
   | `TY_PASSWORD_HASH` | Copy the matching hash from `.env`. |
   | `PAYMENT_INSTRUCTIONS` | Your customer-facing PayNow or bank-transfer directions. |

   Keep these values in Render's private settings, not in a public file or message. The `.env` file is not included in the ZIP and must not be committed to GitHub.
6. Deploy the service. The private tracker is at its `onrender.com` address; customers open the welcome page at the same address followed by `/order`. Share the main address with your partner privately and send their password separately. Share the `/order` address with customers.

Both partners now use the same online database. Sign out on shared devices. If you change either password, rerun `python setup_auth.py` and update the matching hash and secret in Render.

## Data safety and limits

- Back up business records regularly. A hosting disk is not a substitute for a backup. Downloading a database backup can be added as a follow-up feature.
- If you already entered records in the local version, the online service starts with a new database. Do not delete the local project; the records need to be copied into the hosted database separately.
- This simple SQLite setup is designed for two partners and one running web-service instance. Do not add multiple server instances or remove the persistent disk.
- A login makes the tracker private to people with the passwords, but the website address itself is reachable from the internet. Use strong, different passwords and avoid recording sensitive customer details.
- The customer order page is public by design. It collects a name, a phone/email or Telegram username, pickup preference, and order notes. Telegram is saved as contact information only; the app does not send Telegram messages. Orders wait for you to verify payment; only then does the tracker add the sale to revenue.
- Payment screenshots are restricted to PNG/JPG, resized and stripped of image metadata, and available only through the signed-in tracker. They are saved on the persistent disk, so also include them in your backup plan.
- This version uses manual PayNow/bank-transfer confirmation. It does not connect to a bank, accept card payments, or send automatic order notifications. Check the private Orders tab for new requests. A single contact field accepts a phone number, email, or Telegram @username.

## What the tracker records

- **Overview:** total revenue, total costs, profit or loss, and recent batch performance.
- **Batches:** each production batch can include multiple 400 g pints of one flavour. When adding a batch, set how many pints it made, that batch’s scoop count per pint, who prepared it, and its recipe/mix-in version. The scoop count starts at 8 and can be changed. You can edit the pint and scoop counts later, as long as the change does not reduce stock below what has already sold. Deleting a batch also deletes its linked costs and sales. A batch used by a confirmed customer order can be deleted after that order is cancelled. Sales reduce the batch’s remaining quantity. The public menu does not display stock counts. Owners can add or remove customer-menu flavours from the Customer page tab; flavours already used in old records are removed from the menu while their business history remains.
- **Costs:** record purchase date, batch (optional for shared costs), category, description, quantity, cost, and who paid. Remove an individual cost from the Costs page or delete all costs linked to a batch by deleting that batch.
- **Sales:** record date, batch/flavour, quantity, whether it was a scoop, personal size, or pint, buyer, and which partner recorded the sale. Scoop prices are Milo, Cookies and cream, and Chocolate at S$1.50; Banana pudding at S$2.00. A 100 g personal size is set to two scoop prices (S$3.00 or S$4.00, depending on flavour). Remove a manual sale from the Sales page. To remove a sale created by a customer order, cancel that order first. Owners control which sizes and event/pickup ordering are enabled from the Customer page tab. The current pint price is S$10.00.
- **Customer loyalty:** 50 points gives 50% off an order; 75 points gives 65% off; 100 points will give one free 400 g pint after pint orders launch. Only one reward can be requested per order. Points are reserved while an order is unpaid, redeemed only after payment confirmation, and earned at one point per full S$1 paid after discount. Owners can delete a loyalty account and its points history from the Loyalty page; this keeps the customer’s orders and sales. Accounts with points reserved by an unpaid reward order must have that order cancelled or deleted first.
- **Customer orders:** cancel any unpaid order to release reserved loyalty points. Cancelling a paid order removes its sales (restoring stock), reverses its loyalty points, and marks it cancelled; handle any customer refund separately. Delete an unpaid or cancelled order request permanently with its Delete request button. A paid order must be cancelled before it can be deleted. Deleting an order also removes its saved payment screenshot and order-related loyalty ledger lines.
- **Pickup status:** after confirming payment, use the button on the private Customer orders page to mark an order “Ready for pickup”. The customer can revisit the secret order link they received to view or refresh that status. Keep that link private because it identifies their order.
- Customer loyalty uses the contact detail entered at checkout as its account ID. The customer can check available points from the order form; balances and reward choices appear after an earlier order earned points. They should use the same contact method and detail each time. There is no customer login or one-time code, so check that the contact belongs to the customer before approving a reward. Full balances are visible in the signed-in tracker.
- **Menu nutrition:** the customer page shows nutrition totals for one 400 g pint recipe only. Cookies and cream shows the supplied Oreo with-filling and without-filling values; Chocolate shows the supplied with-biscuit and without-biscuit values. Milo and Banana pudding show the approximate values supplied by the owners. The note says the base recipes contain 0 g added sugar, except when choosing Oreo, Nutella, or Milo mix-ins; saturated fat is omitted.

Current scoop prices are S$1.50 for Milo, Cookies and cream, and Chocolate, and S$2.00 for Banana pudding. Pints are S$10.00 each. In the private tracker, open Customer page to choose whether customers can order by scoop or pint and whether you are taking event or pickup orders. The same page lets you add flavours, update a flavour price/description by entering its name again, remove flavours from the public menu, and set your Telegram channel link. Pickup requests require a Monday–Friday date at least four days after the order date and one of the pickup areas set for Cass and Tyler. Pickup areas are managed in the Customer page tab: each owner can have several, and you can add, edit or delete them any time. Flavour pictures are added, replaced or deleted from the same tab's Menu flavours table and show on the customer menu. Customers can order even when a flavour has no stock; choose or add a matching batch when you confirm payment. The customer page does not show stock counts.

## Personalise it

At the top of `app.py`, edit `BUSINESS_NAME`, `PARTNERS`, and `COST_CATEGORIES`. Flavour names, descriptions, and scoop prices can be managed in the signed-in tracker under Customer page. If you change the partner names, also change the matching account names in `setup_auth.py`. If you change categories, update the batch totals query and table columns too. Change the colors and spacing in `static/style.css`.


## Latest changes (v8)

- **Customer page tab (owner):** edit a flavour's name, price, description and picture directly in the Menu flavours table; set which flavours and exact versions are sold at events; manage several pickup areas per owner; upload or replace the payment QR code and edit the payment instructions shown on the customer payment page.
- **Pickup orders** are accepted even with no stock (you make batches after customers order). **Event orders** show only the event ice cream you chose, with no mix-in choice, and still need matching stock.
- Event orders never show pickup area, date or time.
- Uploaded flavour pictures and the QR code are stored in `instance/flavour_photos/`. On Render, set `FLAVOUR_PHOTOS_FOLDER=/var/data/flavour_photos`.
