from sgs import SGS

sgs = SGS()
ipca = sgs.fetch(433, start='2020-01-01')
print(ipca.head())
