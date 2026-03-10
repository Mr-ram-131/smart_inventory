import pandas as pd
from sklearn.linear_model import LinearRegression
import pickle

# Sample historical data
data = pd.DataFrame({
    'current_stock': [5, 10, 15, 20, 8, 12, 18],
    'weekly_sales': [3, 6, 10, 12, 4, 7, 11]
})

X = data[['current_stock']]
y = data['weekly_sales']

model = LinearRegression()
model.fit(X, y)

with open('stock_model.pkl', 'wb') as f:
    pickle.dump(model, f)

print("Model trained and saved successfully.")