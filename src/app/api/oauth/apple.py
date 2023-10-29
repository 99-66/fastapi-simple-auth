import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Request, Depends, Form, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

import crud
from core.config import TEMPLATES, Settings, get_settings
from core.responses import ErrorJSONResponse
from dependencies.database import get_session
from dependencies.http import get_http_session
from schemas import (
    UserInsertSchema,
    OAuthUserInsertSchema,
    TokenInsertSchema,
    LoginHistorySchema,
    TokenSchema,
    ErrorResponse,
)
from utils.constants.oauth import ProviderID
from utils.oauth.apple import AppleOAuthClient
from utils.security.encryption import AESCipher, Hasher
from utils.security.token import create_new_jwt_token
from utils.strings import masking_str

router = APIRouter(prefix="/apple", tags=["OAuth"])


@router.get("/login/page")
async def sample_login_page(
    *, request: Request, settings: Settings = Depends(get_settings)
):
    return TEMPLATES.TemplateResponse(
        "apple/login.html",
        {
            "request": request,
            "client_id": settings.apple_client_id,
            "redirect_uri": settings.apple_redirect_uri,
        },
    )


@router.post(
    "/login/callback",
    response_model=TokenSchema,
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def apple_login_callback(
    *,
    request: Request,
    state: str = Form(...),
    code: str = Form(...),
    id_token: str = Form(...),
    user: str = Form(default={}),
    error: str = Form(default=None),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
):
    """
    Apple OAuth 로그인 콜백 API
    """

    aes = AESCipher()
    user_dal = crud.UserDAL(session=session)
    oauth_user_dal = crud.SocialUserDAL(session=session)
    user_login_dal = crud.UserLoginHistoryDAL(session=session)
    token_dal = crud.TokenDAL(session=session)

    provider_id = ProviderID.APPLE.name

    ############################
    # OAuth Token check
    ############################
    name = None
    given_name = None
    family_name = None

    # 최초 로그인 한 사용자는 이름 정보가 전달된다
    try:
        user = json.loads(user)
        given_name = user.get('name', {}).get('firstName')
        family_name = user.get('name', {}).get('lastName')
        name = f"{family_name}{given_name}"
    except TypeError:
        pass

    client = AppleOAuthClient(
        code=code, settings=settings, http_session=await get_http_session()
    )
    try:
        user_info = await client.login()
    except Exception as e:
        logger.exception(e)
        await session.rollback()
        return ErrorJSONResponse(
            message="로그인을 처리하는 도중에 문제가 발생하였습니다",
            success=False,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code=1500,
        )

    user_info.name = name

    ############################
    # OAuth User check
    ############################
    new_user_id = None

    # 연동된 계정이 존재하지 않는다면, 신규 사용자 정보를 추가한다
    if not await oauth_user_dal.exists_user(provider_id=provider_id, sub=user_info.id):
        mobile, mobile_key = None, None
        if user_info.mobile:
            mobile = aes.encrypt(user_info.mobile)
            mobile_key = Hasher.hmac_sha256(user_info.mobile)

        email, email_key = None, None
        if user_info.email:
            email = aes.encrypt(user_info.email)
            email_key = Hasher.hmac_sha256(user_info.email)

        # 신규 사용자 정보를 생성한다
        new_user = UserInsertSchema(
            name=user_info.name,
            email=email,
            email_key=email_key,
            uuid=uuid.uuid4().bytes,
            mobile=mobile,
            mobile_key=mobile_key,
            password=None,
            provider_id=provider_id,
            is_active=1,
        )

        try:
            # 신규 사용자를 추가한다
            result = await user_dal.insert_user(new_user=new_user)
            new_user_id = result.inserted_primary_key[0]

            # OAuth 사용자 정보를 추가한다
            new_oauth_user = OAuthUserInsertSchema(
                user_id=new_user_id,
                provider_id=provider_id,
                sub=user_info.id,
                name=user_info.name,
                nickname=user_info.nickname,
                profile_picture=user_info.profile_image,
                given_name=given_name,
                family_name=family_name,
            )
            await oauth_user_dal.insert_user(new_user=new_oauth_user)

            await session.commit()
        except Exception as e:
            logger.exception(e)
            await session.rollback()
            await session.close()

            return ErrorJSONResponse(
                message="회원가입을 처리하는 도중에 문제가 발생하였습니다",
                success=False,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_code=1500,
            )

    # 토큰을 생성하기 위해 사용자 정보를 불러온다
    if new_user_id:
        login_user = await user_dal.get_by_user_id(user_id=new_user_id)
    else:
        # 이미 추가된 사용자의 경우에는 oauth openid 정보로 사용자를 조회한다
        login_user = await oauth_user_dal.get_user(
            provider_id=provider_id, sub=user_info.id
        )

    if not login_user:
        logger.info(
            f'사용자를 찾을 수 없습니다. { {"email": masking_str(user_info.email), "provider_id": provider_id} }'
        )
        return ErrorJSONResponse(
            message="사용자를 찾을 수 없습니다",
            success=False,
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=1404,
        )

    if not login_user.is_active:
        logger.info(
            f'사용할 수 없는 아이디입니다. { {"email": masking_str(user_info.email), "provider_id": provider_id} }'
        )
        return ErrorJSONResponse(
            message="사용할 수 없는 아이디입니다",
            success=False,
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code=1400,
        )

    ############################
    # Create AccessToken
    ############################
    # JWT Token 쌍을 생성한다
    new_token = await create_new_jwt_token(sub=str(login_user.id))

    # 생성한 RefreshToken을 DB에 저장하기 위한 스키마 생성
    new_refresh_token = TokenInsertSchema(
        user_id=login_user.id,
        access_token=new_token.access_token,
        refresh_token=aes.encrypt(new_token.refresh_token),
        refresh_token_key=Hasher.hmac_sha256(new_token.refresh_token),
        issued_at=datetime.fromtimestamp(int(new_token.iat)),
        expires_at=datetime.fromtimestamp(int(new_token.refresh_token_expires_in)),
    )

    try:
        # Token 정보 저장
        await token_dal.insert_token(new_token=new_refresh_token)

        # Login 이력 저장
        await user_login_dal.insert_login_history(
            login_history=LoginHistorySchema(
                user_id=login_user.id,
                login_time=datetime.now(),
                login_success=True,
                ip_address=request.client.host,
            )
        )

        await session.commit()
    except Exception as e:
        logger.exception(e)
        await session.rollback()
        return ErrorJSONResponse(
            message="로그인을 처리하는 도중에 문제가 발생하였습니다",
            success=False,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code=1500,
        )
    finally:
        await session.close()

    logger.info(
        f'사용자가 로그인하였습니다. { {"user_id": login_user.id, "email": masking_str(user_info.email), "provider_id": provider_id} }'
    )

    response = TokenSchema(**new_token.model_dump())

    return response
