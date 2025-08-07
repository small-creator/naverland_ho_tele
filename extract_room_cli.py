#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네이버 부동산 동호수 추출 CLI 도구
GitHub Actions에서 실행 가능한 버전
"""

import requests
import json
import re
import sys
import argparse
import os
import time
from typing import Optional, Tuple

class PropertyExtractor:
    def __init__(self):
        # 환경변수에서 인증 정보를 가져오거나 기본값 사용
        self.bearer_token = os.getenv('NAVER_BEARER_TOKEN', "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6IlJFQUxFU1RBVEUiLCJpYXQiOjE3NTQ0Nzg5OTcsImV4cCI6MTc1NDQ4OTc5N30.cKqU9FGZyzMMw-6oNvcLsNxJ0u7nTKL_9tW4kzEWACs")
        
        # 최신 쿠키 사용 (2025-01-06 업데이트)
        self.cookies = {
            'NNB': 'F4S2HFLS4FKGQ',
            '_fwb': '87fDswuFKmaDe1QbAXmkGO.1750463894199',
            '_ga': 'GA1.2.2038969098.1750636492',
            'ASID': '76dff910000001979b32743d0000001b',
            'NAC': 'CR27BoA2gLYq',
            'landHomeFlashUseYn': 'Y',
            'SHOW_FIN_BADGE': 'Y',
            'bnb_tooltip_shown_finance_v1': 'true',
            'nhn.realestate.article.rlet_type_cd': 'A01',
            'nhn.realestate.article.trade_type_cd': '""',
            'nhn.realestate.article.ipaddress_city': '2700000000',
            'NACT': '1',
            'SRT30': '1754478762',
            'SRT5': '1754478762',
            'BUC': 'IQItBioFcGBQGFrF0qfij3hnpJsL4xj8Pz7kfMx3D28=',
            'REALESTATE': 'Wed%20Aug%2006%202025%2020%3A16%3A37%20GMT%2B0900%20(Korean%20Standard%20Time)',
            'PROP_TEST_KEY': '1754478997702.6bc9fd77ec2a23d42b4af092007c3dcf6d8bfa67965437be587c1d5fdbb1c331',
            'PROP_TEST_ID': '1c6a95acfa4aa931ef3745c6c65ddfd69539af9e2be07d491a5b5c9914ef6cfc'
        }
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.8',
        })
        
        # 세션에 쿠키 추가 (GUI와 동일하게)
        self.session.cookies.update(self.cookies)
    
    def get_broker_id(self, article_no: str) -> Optional[str]:
        """네이버 부동산 API에서 realtorId(brokerId) 추출"""
        try:
            headers = {
                'authorization': f'Bearer {self.bearer_token}',
                'accept': '*/*',
                'accept-language': 'ko,en-US;q=0.9,en;q=0.8,no;q=0.7',
                'cache-control': 'no-cache',
                'pragma': 'no-cache',
                'referer': f'https://new.land.naver.com/articles/{article_no}',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            cookie_string = '; '.join([f'{k}={v}' for k, v in self.cookies.items()])
            headers['cookie'] = cookie_string
            
            url = f'https://new.land.naver.com/api/articles/{article_no}'
            params = {'complexNo': ''}
            
            response = requests.get(url, headers=headers, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                return self.extract_realtor_id_from_data(data)
            else:
                print(f"API 호출 실패 (HTTP {response.status_code})")
                return None
                
        except Exception as e:
            print(f"오류 발생: {str(e)}")
            return None
    
    def extract_realtor_id_from_data(self, data):
        """데이터에서 realtorId 추출 (여러 경로 확인)"""
        try:
            if not isinstance(data, dict):
                return None
            
            # 여러 경로에서 realtorId 찾기
            paths = [
                ['articleAddition', 'articleRealtor', 'realtorId'],
                ['articleAddition', 'realtorId'],
                ['articleRealtor', 'realtorId'],
                ['realtorId']
            ]
            
            for path in paths:
                current = data
                for key in path:
                    if isinstance(current, dict) and key in current:
                        current = current[key]
                    else:
                        current = None
                        break
                
                if current:
                    return current
            
            return None
            
        except Exception:
            return None
    
    def convert_to_eok(self, price_info: str, trade_type: str) -> str:
        """가격을 억단위로 변환"""
        if not price_info:
            return ""
            
        try:
            if trade_type == '월세' and '/' in price_info:
                parts = price_info.split('/')
                deposit = int(parts[0])
                monthly = parts[1].strip()
                
                if deposit >= 10000:
                    deposit_eok = deposit / 10000
                    return f"보증금 {deposit_eok:.1f}억 / 월세 {monthly}만원"
                else:
                    return f"보증금 {deposit:,}만원 / 월세 {monthly}만원"
            else:
                price = int(price_info)
                if price >= 10000:
                    eok = price / 10000
                    return f"{eok:.1f}억"
                else:
                    return f"{price:,}만원"
        except (ValueError, TypeError):
            return price_info
    
    def get_property_details(self, broker_id: str, article_no: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """매물의 상세 정보 가져오기"""
        try:
            for page in range(1, 11):
                url = "https://m.land.naver.com/agency/info/list"
                params = {
                    'rltrMbrId': broker_id,
                    'tradTpCd': '',
                    'atclRletTpCd': '',
                    'tradeTypeChange': 'false',
                    'page': page
                }
                
                response = self.session.get(url, params=params, timeout=10)
                
                if response.status_code != 200:
                    continue
                
                data = response.json()
                properties = data.get('list', [])
                
                if not properties:
                    break
                
                for prop in properties:
                    if prop.get('atclNo') == article_no:
                        complex_name = prop.get('atclNm', '')
                        price_info = prop.get('prcInfo', '')
                        trade_type = prop.get('tradTpNm', '')
                        dtl_addr = prop.get('dtlAddr', '')
                        
                        formatted_price = self.convert_to_eok(price_info, trade_type)
                        return complex_name, formatted_price, dtl_addr
                
                if len(properties) < data.get('pageSize', 20):
                    break
                
                time.sleep(0.3)
            
            return None, None, None
            
        except Exception as e:
            print(f"매물 검색 오류: {str(e)}")
            return None, None, None
    
    def extract_room_info(self, dtl_addr: str) -> Tuple[Optional[str], Optional[str], str]:
        """dtlAddr에서 동호수 정보 추출"""
        if not dtl_addr:
            return None, None, dtl_addr
            
        dong = None
        ho = None
        
        dong_match = re.search(r'(\d+)동', dtl_addr)
        if dong_match:
            dong = dong_match.group(1) + '동'
            
        ho_match = re.search(r'(\d+)호', dtl_addr)
        if ho_match:
            ho = ho_match.group(1) + '호'
            
        return dong, ho, dtl_addr
    
    def extract_room(self, article_no: str, verbose: bool = False) -> dict:
        """매물번호로부터 동호수 정보 추출"""
        result = {
            'article_no': article_no,
            'complex_name': None,
            'price': None,
            'dong': None,
            'ho': None,
            'full_address': None,
            'success': False,
            'error': None
        }
        
        if verbose:
            print(f"Article No: {article_no}")
            print("=" * 50)
        
        try:
            # Step 1: broker ID 추출
            if verbose:
                print("Getting realtorId...")
            
            broker_id = self.get_broker_id(article_no)
            if not broker_id:
                result['error'] = "realtorId를 찾을 수 없습니다"
                return result
            
            if verbose:
                print(f"RealtorId found: {broker_id}")
            
            # Step 2: 매물 상세 정보 검색
            if verbose:
                print("Getting property details...")
            
            complex_name, price, dtl_addr = self.get_property_details(broker_id, article_no)
            if not dtl_addr:
                result['error'] = "매물 정보를 찾을 수 없습니다"
                return result
            
            # Step 3: 동호수 추출
            dong, ho, full_addr = self.extract_room_info(dtl_addr)
            
            result.update({
                'complex_name': complex_name,
                'price': price,
                'dong': dong,
                'ho': ho,
                'full_address': full_addr,
                'success': True
            })
            
            if verbose:
                print(f"Property found!")
                print(f"   Complex: {complex_name}")
                print(f"   Price: {price}")
                print(f"   Address: {full_addr}")
                print(f"   Building: {dong or 'N/A'}")
                print(f"   Unit: {ho or 'N/A'}")
            
            return result
            
        except Exception as e:
            result['error'] = str(e)
            return result

def main():
    parser = argparse.ArgumentParser(description='네이버 부동산 동호수 추출 도구')
    parser.add_argument('article_no', help='매물번호')
    parser.add_argument('-v', '--verbose', action='store_true', help='상세한 출력')
    parser.add_argument('-j', '--json', action='store_true', help='JSON 형태로 출력')
    
    args = parser.parse_args()
    
    if not args.article_no.isdigit():
        print("❌ 매물번호는 숫자만 입력해주세요.")
        sys.exit(1)
    
    extractor = PropertyExtractor()
    result = extractor.extract_room(args.article_no, args.verbose)
    
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        if result['success']:
            print(f"Success!")
            print(f"Article No: {result['article_no']}")
            print(f"Complex: {result['complex_name'] or 'N/A'}")
            print(f"Price: {result['price'] or 'N/A'}")
            print(f"Building: {result['dong'] or 'N/A'}")
            print(f"Unit: {result['ho'] or 'N/A'}")
            print(f"Address: {result['full_address'] or 'N/A'}")
        else:
            print(f"Failed: {result['error']}")
            sys.exit(1)

if __name__ == "__main__":
    main()