"""Creates demo_business.db: a small sample business database to try the agent's database tools.
Run once from the backend folder:  python make_demo_db.py
Then add to .env:
  AGENT_DATABASE_URL=sqlite:///demo_business.db
  AGENT_DB_TABLES=customers,products,orders
"""
import random
import sqlite3
from datetime import date, timedelta

random.seed(7)
db = sqlite3.connect("demo_business.db")
db.executescript("""
DROP TABLE IF EXISTS orders; DROP TABLE IF EXISTS products; DROP TABLE IF EXISTS customers;
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT, city TEXT, joined DATE);
CREATE TABLE products(id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL);
CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER, product_id INTEGER, quantity INTEGER, order_date DATE, status TEXT);
""")
cities = ["Chennai", "Trichy", "Madurai", "Coimbatore", "Salem"]
names = ["Arun", "Divya", "Karthik", "Meena", "Suresh", "Priya", "Vijay", "Lakshmi", "Ravi", "Anitha", "Bala", "Kavya"]
db.executemany("INSERT INTO customers VALUES(?,?,?,?)",
               [(i + 1, n, random.choice(cities), str(date(2025, 1, 1) + timedelta(days=random.randint(0, 600)))) for i, n in enumerate(names)])
products = [("Laptop", "Electronics", 55000), ("Phone", "Electronics", 22000), ("Headphones", "Electronics", 3500),
            ("Desk", "Furniture", 8500), ("Chair", "Furniture", 6200), ("Notebook", "Stationery", 120), ("Pen set", "Stationery", 250)]
db.executemany("INSERT INTO products VALUES(?,?,?,?)", [(i + 1, *p) for i, p in enumerate(products)])
db.executemany("INSERT INTO orders VALUES(?,?,?,?,?,?)", [
    (i + 1, random.randint(1, len(names)), random.randint(1, len(products)), random.randint(1, 4),
     str(date(2026, 1, 1) + timedelta(days=random.randint(0, 280))), random.choice(["delivered", "delivered", "delivered", "shipped", "cancelled"]))
    for i in range(120)])
db.commit()
print("Created demo_business.db with customers, products and orders.")
