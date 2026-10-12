import pandas as pd
import numpy as np

df = pd.read_csv('daily2YearSPXL.csv')

#df['difference'] = df.apply(lambda row: row.close - row.open, axis=1)
df['timestamp'] = pd.to_datetime(df['date'])
df['close'] = df['close'].astype('float')
df['open'] = df['open'].astype('float')
df['difference'] = df['timestamp'].diff()
df['yesterday_close'] = df['close'].shift()
df['price_diff_open_real'] = df['open'] - df['yesterday_close']
df['price_diff_open'] = df['low'] - df['yesterday_close']
df['price_diff_close'] = df['high'] - df['yesterday_close']
df['daily_diff_perc'] = ((df['close'] - df['open'])/df['open'])*100
df['daily_diff'] = ((df['close'] - df['open']))
query = f'difference.dt.days>0 and ((price_diff_open < 80 or price_diff_close < 80 )and (price_diff_close >-80 or price_diff_open>-80))' 
#query = f'difference.dt.days>0 and ((price_diff_open_real < 20  )and ( price_diff_open_real>-20))'
query = f'price_diff_open_real>50 or price_diff_open_real<-50'
#query  =f'yesterday_close<high and yesterday_close>low'                                   
df =df.query(query)
#df.diff()
df = df.sort_values('price_diff_open')
print(df.to_string()) 
print(df.size)