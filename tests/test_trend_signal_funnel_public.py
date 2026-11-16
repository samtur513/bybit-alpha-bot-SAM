"""No-network tests for auxiliary public OHLCV trend signal funnel audit."""
import unittest
import pandas as pd
import numpy as np
from tools.audit_trend_signal_funnel_public import (
    funnel_reason, indicators, analyze_bars, HOUR
)
from v3_2.src.regime_classifier import MarketRegimeClassifier
from v3.src.multi_scanner import MultiCoinScanner


class FunnelTests(unittest.TestCase):
    def setUp(self):
        self.scanner=MultiCoinScanner(min_adx=18.0,min_volume_ratio=1.2)
        self.bar={"close":105.0,"upper":100.0,"lower":90.0,
                  "ema_fast":102.0,"ema_slow":97.0,"atr":2.0,"volume":150.0,
                  "volume_sma":100.0,"adx":25.0}

    def test_actual_scanner_long_parity(self):
        self.assertEqual(funnel_reason(self.bar,self.scanner),("LONG_CANDIDATE",1))
        self.assertEqual(self.scanner.evaluate_symbol_bar(
          symbol="BTCUSDT",close=105.0,upper_channel=100.0,lower_channel=90.0,
          ema_fast=102.0,ema_slow=97.0,atr=2.0,volume=150.0,
          volume_sma=100.0,adx=25.0).direction,1)

    def test_reason_bins(self):
        tests=[({"adx":17},"ADX_BELOW_18"),
               ({"volume":118},"RVOL_BELOW_1_2"),
               ({"upper":110},"NO_120H_DONCHIAN_BREAKOUT"),
               ({"ema_fast":90},"LONG_EMA_DISAGREES"),
               ({"upper":float("nan")},"INVALID_INDICATORS")]
        for changed,expected in tests:
            self.assertEqual(funnel_reason({**self.bar,**changed},self.scanner)[0],expected)

    def test_exact_shifted_120h_channel(self):
        data=[]
        for i in range(150):
            data.append({"timestamp":i*HOUR,"high":120 if i==148 else 110,
                         "low":80,"open":101,"close":100,"volume":100,
                         "turnover":10000})
        df=indicators(pd.DataFrame.from_records(data),MarketRegimeClassifier())
        self.assertEqual(float(df.loc[149,"upper"]),120.0)
        self.assertNotEqual(float(df.loc[149,"high"]),float(df.loc[149,"upper"]))

    def test_no_trade_or_db_mutation_for_synthetic_flat_data(self):
        data=pd.DataFrame([{"timestamp":i*HOUR,"high":110.0,"low":90.0,"open":100.0,
                            "close":100.0,"volume":100,"turnover":10000} for i in range(160)])
        result=analyze_bars({"BTCUSDT":data},100*HOUR,160*HOUR)
        self.assertEqual(result["trend_candidates"]["live_since_T0"],0)
        self.assertGreater(result["signal_funnel"]["live_since_T0"].get("INVALID_INDICATORS",0),0)


if __name__=="__main__":
    unittest.main()
