import sys, os, math, time
import logging.config
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler

# 현재 스크립트의 디렉토리를 기반으로 logging.conf의 경로를 절대 경로로 지정
current_dir = os.path.dirname(os.path.abspath(__file__))
logging_conf_path = os.path.join(current_dir, 'logging.conf')

# 로그 설정을 UTF-8로 강제 읽기
logging.config.fileConfig(logging_conf_path, encoding='utf-8')
logger = logging.getLogger(__name__)

# account, upbit_data, trading, utils 디렉토리의 경로를 생성
account_dir = os.path.join(current_dir, 'account')
upbit_data_dir = os.path.join(current_dir, 'upbit_data')
trading_dir = os.path.join(current_dir, 'trading')
utils_dir = os.path.join(current_dir, 'utils')

# sys.path에 account 디렉토리를 추가
sys.path.append(account_dir)
sys.path.append(upbit_data_dir)
sys.path.append(trading_dir)
sys.path.append(utils_dir)

# import
from account.my_account import get_my_exchange_account
from upbit_data.candle import get_min_candle_data
# from trading.trading_strategy import trading_strategy
from trading.trading_strategy2 import trading_strategy
from trading.trade import buy_market, sell_market, get_open_order
from utils.email_utils import send_email

# 전역변수
buy_time = None  # 매수시간
krw_balance = 0  # 계좌잔고(KRW)

# 로그파일 경로
log_dir = os.path.join(current_dir, 'logs')

# 로그 폴더가 없으면 생성
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
    print(f'로그 폴더 생성: {log_dir}')

def get_account_info():
    logger.info('========== get_account_info ==========')

    # get my account
    my_account = get_my_exchange_account()

    # 도지코인(DOGE) 기준으로 확인합니다. -> 솔라나(SOL)로 변경 + 도지코인(DOGE), 비트코인(BTC) 추가
    sol_ticker = 'SOL'
    is_sol_in_account = False
    sol_balance = '0'
    sol_avg_buy_price = 0.0

    if 'currency' not in my_account.columns:
        raise ValueError('[currency] 컬럼이 존재하지 않습니다.')

    if sol_ticker in my_account['currency'].values:
        is_sol_in_account = True
        sol_balance = my_account[my_account['currency'] == sol_ticker]['balance'].values[0]
        sol_avg_buy_price = float(my_account[my_account['currency'] == sol_ticker]['avg_buy_price'].values[0])

    logger.debug(f'is_sol_in_account : {is_sol_in_account}')
    logger.debug(f'sol_balance : {sol_balance}')
    logger.debug(f'sol_avg_buy_price : {sol_avg_buy_price}')

    # 원화 잔고 확인
    krw_amount = 0.0
    krw_ticker = 'KRW'
    if krw_ticker in my_account['currency'].values:
        my_account['balance'] = my_account['balance'].astype(float)
        krw_amount = my_account[my_account['currency'] == krw_ticker]['balance'].values[0]

    logger.debug(f'krw_amount : {krw_amount}')

    # 투자 가능한 원화 계산
    krw_invest_amount = math.floor(krw_amount * 0.999) if krw_amount > 0 else 0
    logger.debug(f'krw_invest_amount : {krw_invest_amount}')

    return {
        'is_sol': is_sol_in_account,
        'sol_balance': sol_balance,
        'sol_buy_price': sol_avg_buy_price,
        'krw_balance': krw_amount,
        'krw_available': krw_invest_amount
    }

def check_time():
    current_time = datetime.now()
    current_minute = current_time.minute
    is_multiple_of_five = current_minute % 5 == 0

    logger.debug(f'current_time : {current_time}, current_minute : {current_minute}')
    logger.debug(f'is_multiple_of_five : {is_multiple_of_five}')

    return is_multiple_of_five

def get_data():
    return get_min_candle_data('KRW-SOL', 5)

def auto_trading():
    try:
        account_info = get_account_info()
        multiple_of_five = check_time()
        current_position = 1 if account_info['is_sol'] else 0
        logger.debug(f'current_position : {current_position}')

        global buy_time, krw_balance

        if current_position == 0 and multiple_of_five:
            trade_strategy_result = trading_strategy(get_data(), current_position)
            logger.debug(f'trade_strategy_result : {trade_strategy_result}')

            if trade_strategy_result['signal'] == 'buy':
                krw_balance = math.floor(account_info['krw_balance'])
                buy_result = buy_market('KRW-SOL', account_info['krw_available'])

                if buy_result['uuid'].notnull()[0]:
                    min5_ago = datetime.now() - timedelta(minutes=5)
                    buy_time = min5_ago.replace(second=0, microsecond=0).strftime('%Y-%m-%d %H:%M:%S')
                    logger.info(f'[KRW-SOL] {account_info["krw_available"]}원 매수 하였습니다.')

                    send_email('[KRW-SOL] 시장가 매수', trade_strategy_result['message'])
                else:
                    logger.error('매수가 정상적으로 처리되지 않았습니다.')

        elif current_position == 1 and multiple_of_five:
            trade_strategy_result = trading_strategy(get_data(), current_position, buy_time, account_info['sol_buy_price'])
            logger.debug(f'trade_strategy_result : {trade_strategy_result}')

            if trade_strategy_result['signal'] == 'sell':
                sell_result = sell_market('KRW-SOL', account_info['sol_balance'])
                if sell_result['uuid'].notnull()[0]:
                    while True:
                        open_order_df = get_open_order('KRW-SOL', 'wait')
                        print(open_order_df)
                        time.sleep(5)
                        if len(open_order_df) == 0:
                            break

                    after_sell_account = get_my_exchange_account()
                    print(after_sell_account)

                    trade_result = 0
                    if 'KRW' in after_sell_account['currency'].values:
                        after_sell_krw_bal = math.floor(float(after_sell_account[after_sell_account['currency'] == 'KRW']['balance'].values[0]))
                        trade_result = math.floor(after_sell_krw_bal - krw_balance)

                    logger.info(f'[KRW-SOL] {account_info["sol_balance"]} 매도 하였습니다.')
                    logger.info(f'매매수익은 {trade_result} 입니다.')

                    buy_time = None
                    krw_balance = 0

                    send_email('[KRW-SOL] 시장가 매도', f'{trade_strategy_result["message"]}\n매매수익은 {trade_result} 입니다.')
                else:
                    logger.error('매도가 정상적으로 처리되지 않았습니다.')
                    send_email('매도 중 에러 발생', '매도 중 에러가 발생하였습니다. 확인해주세요.')

    except ValueError as ve:
        logger.error(f'ValueError : {ve}')
    except Exception as e:
        logger.error(f'예상치 못한 오류 발생 : {e}')

if __name__ == '__main__':
    logger.info('++++++++++ apscheduler starts. ++++++++++')
    scheduler_start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    logger.info(f'scheduler_start_time : {scheduler_start_time}')

    scheduler = BackgroundScheduler()
    scheduler.add_job(auto_trading, 'cron', second=5)
    scheduler.start()

    try:
        while True:
            time.sleep(2)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
