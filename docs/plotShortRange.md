# plotShortRange
```python
plotShortRange(df: pandas.core.frame.DataFrame, comid: int, flow: bool = True)
```

Function for plotting short range forecasts. Set `flow = False` if plotting stage. `df` is the output of `ShortRange.get_streamflow()`.